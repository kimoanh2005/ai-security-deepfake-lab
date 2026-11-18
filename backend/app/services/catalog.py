"""Danh mục Lab mặc định (dùng cho seed). Không import service nào để tránh phụ thuộc vòng.

`status` là TRẦN của result_kind (real > simulated > planned). Các Lab đang là bản khung nên để `simulated`;
chỉ nâng lên `real` khi TV2–TV4 tích hợp thật và có bằng chứng.
`enabled` chỉ là mặc định khi seed lần đầu; sau đó admin đổi qua PATCH /labs/{code}.
"""
from __future__ import annotations

LAB_CATALOG: list[dict] = [
    {"legacy_id": 1, "code": "deepfake", "name": "Deepfake & Identity Spoofing", "status": "simulated", "enabled": 1,
     "description": "Phân tích video để tìm tín hiệu bất thường (nghiên cứu, không kết luận tuyệt đối)."},
    {"legacy_id": 2, "code": "prompt_injection", "name": "Prompt Injection trên Chatbot", "status": "simulated", "enabled": 1,
     "description": "So sánh phản hồi mô hình trước/sau phòng thủ trong sandbox."},
    {"legacy_id": 3, "code": "api_lab", "name": "API Security & Rate Limiting", "status": "simulated", "enabled": 1,
     "description": "Mô phỏng lạm dụng API trên ứng dụng demo nội bộ, so sánh khi bật/tắt giới hạn tần suất."},
    {"legacy_id": 4, "code": "poisoning", "name": "Data Poisoning (Nâng cao)", "status": "planned", "enabled": 0,
     "description": "Dự kiến (ngoài phạm vi MVP)."},
]

LEGACY_IDS: dict[str, int] = {item["code"]: item["legacy_id"] for item in LAB_CATALOG}


def lab_id_for(code: str) -> str:
    return f"lab_{code}"
