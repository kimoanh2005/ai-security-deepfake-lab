# Tài liệu

| Tệp | Dành cho | Nội dung |
|---|---|---|
| [`spec/spec.md`](spec/spec.md) | cả nhóm | SPEC-01: yêu cầu, kiến trúc, nhãn trung thực, mô hình dữ liệu, backlog (v0.10) |
| [`api-contract.md`](api-contract.md) | TV5 (gọi API), TV2–TV4 | API-01: hợp đồng REST, mã lỗi, ví dụ thật, trạng thái triển khai |
| [`integration.md`](integration.md) | TV2, TV3, TV4, TV5 | INT-01 (v0.4): cách viết `LabService`; mục 13: trạng thái tích hợp TV4, cấu hình Colab, payload/kết quả và chấm tay (TV5) |
| [`deepfake-evaluation.md`](deepfake-evaluation.md) | TV3, TV1 | Hướng dẫn detector ONNX, quy trình đo FPR/FNR/Precision và ghi chú khi chưa tích hợp |
| [`spec/diagrams/`](spec/diagrams) | cả nhóm | Sơ đồ kiến trúc, trình tự, trạng thái, ER |

Khi tài liệu mâu thuẫn: `api-contract.md` là nguồn chuẩn cho REST; sau đó là `/openapi.json` do FastAPI sinh từ mã.
