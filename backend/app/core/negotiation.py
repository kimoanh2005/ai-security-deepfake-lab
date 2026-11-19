"""Content negotiation theo header `Accept` (RFC 9110 mục 12.5.1), đủ dùng cho vài định dạng phục vụ cố định."""
from __future__ import annotations


def _parse(accept: str) -> list[tuple[str, str, float]]:
    """Tách thành (type, subtype, q). Bỏ qua khoảng sai cú pháp thay vì ném lỗi."""
    ranges: list[tuple[str, str, float]] = []
    for part in accept.split(","):
        pieces = [p.strip() for p in part.split(";")]
        media = pieces[0].lower()
        if "/" not in media:
            continue
        mtype, _, subtype = media.partition("/")
        if not mtype or not subtype:
            continue
        q = 1.0
        valid = True
        for param in pieces[1:]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    valid = False
                    break
                if not 0.0 <= q <= 1.0:
                    valid = False
                    break
        if valid:
            ranges.append((mtype, subtype, q))
    return ranges


def negotiate(accept: str | None, offered: tuple[str, ...]) -> str | None:
    """Chọn định dạng phục vụ phù hợp nhất; `offered` theo thứ tự ưu tiên của máy chủ (hòa thì lấy đứng trước).

    Không có `Accept` (hoặc rỗng) → định dạng đầu tiên. Trả None nếu không định dạng nào được chấp nhận (→ 406).
    Với mỗi định dạng, khoảng cụ thể nhất khớp với nó quyết định `q` (type/subtype > type/* > */*).
    """
    if accept is None or not accept.strip():
        return offered[0]
    ranges = _parse(accept)
    best: str | None = None
    best_q = 0.0
    for media in offered:
        mtype, _, subtype = media.partition("/")
        specificity, q = 0, 0.0
        for r_type, r_sub, r_q in ranges:
            if r_type == mtype and r_sub == subtype:
                rank = 3
            elif r_type == mtype and r_sub == "*":
                rank = 2
            elif r_type == "*" and r_sub == "*":
                rank = 1
            else:
                continue
            if rank > specificity:
                specificity, q = rank, r_q
        if q > best_q:
            best, best_q = media, q
    return best
