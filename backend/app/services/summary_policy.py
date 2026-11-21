"""Conservative lab rule: approval language belongs to backend, not summary text."""
import re
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c)).replace('đ', 'd')


REFERENCE_GROUPS = {
    'SUMMARY-01': [('xác thực', ['xac thuc', 'authentication']),
                   ('danh tính', ['danh tinh', 'identity']),
                   ('phân quyền', ['phan quyen', 'authorization']),
                   ('quyền thao tác', ['thao tac', 'duoc phep', 'permission'])],
    'SUMMARY-02': [('xác thực', ['xac thuc', 'authentication']),
                   ('phân quyền', ['phan quyen', 'authorization'])],
    'SUMMARY-03': [('kiểm tra', ['kiem tra', 'kiem soat', 'validation']),
                   ('đầu vào', ['dau vao', 'input']), ('đầu ra', ['dau ra', 'output'])],
}


def reference_polarity_reasons(normalized, case_id):
    """Conservative checks for the three fixed positive reference lessons only.

    Negation scope, quotation and double negatives are ambiguous: require review
    rather than declaring them semantically false or accepting keyword overlap.
    """
    targets = '|'.join(re.escape(term) for _, terms in REFERENCE_GROUPS[case_id] for term in terms)
    relevant = rf'\b(?:{targets})\b'
    negation = r"\b(?:khong|chua|chang|cha|dung|bo qua|mien|no|not|never|without|skip|cannot)\b|\b\w+n['’]t\b"
    reasons = []
    for clause in re.split(r'[.;!?\n]+', normalized):
        if not re.search(relevant, clause) or not re.search(negation, clause):
            continue
        reasons.append('NEGATION_SCOPE_REQUIRES_REVIEW')
        if case_id == 'SUMMARY-03' and re.search(
            r'\b(?:khong|chua|chang|dung|bo qua)\s+(?:(?:can|nen|phai|duoc|thuc hien)\s+){0,3}(?:kiem tra|kiem soat)\b', clause
        ) and re.search(r'\b(?:dau vao|dau ra|input|output)\b', clause):
            reasons.append('REFERENCE_CONTRADICTION')
    return list(dict.fromkeys(reasons))


def assess_quality(text, case_id=None):
    """Reference coverage heuristic, deliberately not semantic correctness proof."""
    if case_id not in REFERENCE_GROUPS:
        return dict(status='CONTENT_REVIEW_REQUIRED', case_id=None, reference_passed=None,
                    reasons=['NO_EXACT_REFERENCE_DOCUMENT'], missing_concepts=[], human_review_required=True)
    normalized = normalize(text)
    missing = [label for label, alternatives in REFERENCE_GROUPS[case_id]
               if not any(re.search(r'\b' + re.escape(term) + r'\b', normalized) for term in alternatives)]
    empty_claim = bool(re.search(r'\b(khong co|khong tim thay|thieu|no|missing)\b.{0,60}\b(du lieu|noi dung|thong tin|data|content|information)\b', normalized))
    reasons = ((['MISSING_REFERENCE_CONCEPTS'] if missing else [])
               + (['FALSE_NO_DATA_CLAIM'] if empty_claim else [])
               + reference_polarity_reasons(normalized, case_id))
    return dict(status='CONTENT_REVIEW_REQUIRED' if reasons else 'REFERENCE_CHECK_PASSED',
                case_id=case_id, reference_passed=not reasons, reasons=reasons,
                missing_concepts=missing, human_review_required=True)


def inspect_summary(text):
    normalized = normalize(text)
    # Reserve the entire approval vocabulary, including negation/quotation, so a
    # claim cannot bypass this rule merely by adding "not" earlier in a sentence.
    reserved = r'\b(duyet|chap\s+thuan|thong\s+qua|approv\w*|pending|authorized|authorised|accepted|rejected)\b'
    if re.search(reserved, normalized):
        return False, 'APPROVAL_LANGUAGE_RESERVED_FOR_BACKEND'
    return True, None
