import json
import threading
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.api.prompt import TEST_CASES, DEFAULT_DOCUMENT, PromptRequest, execute
from app.services.lab_service import lab_service, log_security_event
from app.services.api_lab_service import measure_api
from app.services.security_service import security_service

router = APIRouter(tags=['TV1-TV4 experiments'])
lab_service.seed(TEST_CASES, DEFAULT_DOCUMENT)
# Serialize model jobs so multiple experiments do not exhaust Colab memory.
experiment_lock = threading.Lock()


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    case_codes: list[str] = Field(..., min_length=1, max_length=100)
    defenses: list[bool] = Field(default_factory=lambda: [False, True])
    repeat: int = Field(1, ge=1, le=20)
    model: str = Field('security-bot', min_length=1, max_length=120)

    @model_validator(mode='after')
    def paired(self):
        if len(self.defenses) != 2 or set(self.defenses) != {False, True}:
            raise ValueError('An experiment must contain exactly baseline and defense branches')
        if len(set(self.case_codes)) != len(self.case_codes):
            raise ValueError('Duplicate case codes are not allowed; use repeat')
        return self


def run_experiment(identifier, ip):
    with experiment_lock:
        try:
            experiment = lab_service.get(identifier)
            config = experiment['config']
            lab_service.status(identifier, 'running')
            for case in config['case_snapshots']:
                for iteration in range(1, config['repeat'] + 1):
                    for defense in config['defenses']:
                        result = execute(PromptRequest(message=case['prompt'], document_text=case['document_text'],
                                         model=config['model'], use_defense=defense), ip, identifier)
                        lab_service.add_run(identifier, case, defense, iteration, config['model'], result)
            saved = lab_service.get(identifier)
            lab_service.status(identifier, 'completed' if all(r['success'] for r in saved['runs']) else 'completed_with_errors')
        except Exception as exc:
            lab_service.status(identifier, 'failed', type(exc).__name__)
            log_security_event('EXPERIMENT_FAILED', action_taken='ERROR', experiment_id=identifier, details=type(exc).__name__)


@router.get('/api/labs/prompt_injection/cases')
def cases():
    return lab_service.cases()


@router.post('/api/labs/prompt_injection/experiments', status_code=202)
def create_experiment(payload: ExperimentRequest, background: BackgroundTasks, request: Request):
    try:
        result = lab_service.create(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    background.add_task(run_experiment, result['id'], request.client.host if request.client else 'local')
    return result


@router.get('/api/experiments')
def history():
    return lab_service.history()


@router.get('/api/experiments/{identifier}')
def get_experiment(identifier: str):
    try:
        return lab_service.get(identifier)
    except KeyError:
        raise HTTPException(404, 'Experiment not found')


class Judgment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_violated: bool | None


@router.patch('/api/experiments/{identifier}/prompt-runs/{run_id}')
def judge(identifier: str, run_id: str, payload: Judgment):
    try:
        lab_service.judge(identifier, run_id, payload.task_violated)
    except KeyError:
        raise HTTPException(404, 'Prompt run not found in experiment')
    return lab_service.get(identifier)


@router.get('/api/experiments/{identifier}/export')
def export(identifier: str):
    return Response(json.dumps(get_experiment(identifier), ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="experiment.json"'})


class EchoRequest(BaseModel):
    defense: bool = True
    message: str = Field('ping', max_length=200)


@router.post('/sandbox/echo')
def echo(payload: EchoRequest, request: Request):
    # Explicit defense switch belongs to this demo endpoint only.
    ip = request.client.host if request.client else 'local'
    allowed, remaining, reset = security_service.check_rate_limit('sandbox:' + ip) if payload.defense else (True, None, 0)
    headers = {'X-RateLimit-Limit': str(security_service.rate_limit_max_requests),
               'X-RateLimit-Remaining': str(remaining if remaining is not None else 'unlimited'), 'X-RateLimit-Reset': str(reset)}
    if not allowed:
        headers['Retry-After'] = str(reset)
    status = 200 if allowed else 429
    log_security_event('SANDBOX_REQUEST', client_ip=ip, defense_applied=payload.defense, action_taken=str(status))
    return JSONResponse({'message': payload.message if allowed else 'Rate limited'}, status_code=status, headers=headers)


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requests_per_branch: int = Field(10, ge=1, le=100)


@router.post('/api/labs/api_rate_limit/experiments')
async def api_experiment(payload: ApiRequest):
    try:
        result = await measure_api(payload.requests_per_branch)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return lab_service.save_api(result)


@router.get('/api/labs/api_rate_limit/history')
def api_history():
    with security_service.connect() as db:
        return [dict(id=i, created_at=t, **json.loads(data)) for i, t, data in db.execute('SELECT id,created_at,data FROM api_lab_runs ORDER BY created_at DESC LIMIT 100')]
