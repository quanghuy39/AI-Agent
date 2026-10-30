# PROGRESS

Cập nhật: 30/10/2026. Nguồn: git log, code trong repo, tóm tắt cuối ngày của người dùng.

## Trạng thái theo ngày

| Ngày | Trạng thái | Kết quả |
|---|---|---|
| 1 | Xong | Cấu trúc repo, `config.py` đọc `.env`, token R2 đọc / đọc-ghi tách riêng, `storage/r2.py` (chặn `answers/`), 4 bộ test bài 0a, 0b, 1, 2 trên R2. |
| 2 | Xong | Lớp LLM: `ask_model` tự điều phối chuỗi qua 4 hãng (không Router), cache sqlite, usage log, JSON pydantic, anonymize, task_id, FakeLLM, `ask_one_model`; bộ kiểm định 12/10/10 case. 179 test pass. |
| 3 | Xong | `ledger/` (hồ sơ bài ở `output/<bài>/`), usage log nối vào `run_log.jsonl`, Dockerfile build và chạy được. 238 test pass. |
| 4 | Xong | `profiling/` (quality, dictionary, flags, relationships, data_summary), `validators/`, khoảng hợp lệ trong dictionary; `tests.grade_bai0b` đạt 5/5 lỗi cài sẵn. 476 test pass. |
| 5 | Đang làm | Runner, skill phân tích, meta_grader, `analysis.loop`, fit_prompt đã commit (605 pass ở commit 3ed5ab6). Mục 4.1 đã làm nhưng chưa commit (622 pass). |
| 6 | Chưa | Phạm vi theo ghi chú: điểm dừng đầy đủ (dựa `pending_required()`), đồng bộ hồ sơ lên R2, quality_flags thành mục chờ xác nhận. Phạm vi đầy đủ: chưa xác nhận. |
| 7 | Chưa | Chưa xác nhận (thư mục `report/`, `slides/` mới có `__init__.py`). |
| 8 | Chưa | Chưa xác nhận (ghi chú: `LLM_CONFIG_PATH` kiểu Windows không dùng được trong container; `main.py` còn rỗng). |

## Ngày 5

Đã xong (commit d58c0bb..3ed5ab6):
- `runner/run_code.py`: thư mục tạm input (chỉ đọc) / work (`AGENT_OUTPUT_DIR`) / run / meta (`AGENT_META_PATH`);
  bỏ biến `R2_*`, `*_API_KEY`, `LLM_CONFIG_PATH`, `PYTHONPATH`; quét ast (import, đường dẫn cấm); timeout;
  chỉ chép file về `output/<bài>/work/` khi chạy trọn; mỗi lần liệt kê R2, lỗi kết nối thì dùng cache kèm cảnh báo;
  đọc file meta vào `RunResult.meta`.
- `skills/analysis/`: `plan.md`, `step.md`, `contract.md`, `schemas.py` (AnalysisPlan, AnalysisStep, AgentMeta).
- `analysis/meta_grader.py`: `grade_meta` chấm meta bằng `validators/`, trả structural_errors, checks, flags.
- `analysis/loop.py`: lập kế hoạch → xác nhận y/n → giải từng câu (thử lại có error_feedback) → chấm meta → ghi
  ledger; che giá trị cột nhạy cảm; CLI `python -m analysis.loop`.
- `fit_prompt`: bảo vệ độ dài prompt bước giải (cắt previous_results → error_feedback → confirmed_decisions; vẫn
  vượt thì open_item và dừng mã 3, không gọi AI).

Còn lại:
- 4.1 Sửa bộ kiểm định theo skill thật: ĐÃ LÀM trong working tree, chưa commit. Prompt analysis lấy từ
  `render_prompt("step")` + data summary của dữ liệu giả, schema AnalysisStep, chạy code bằng hàm lõi
  `runner.execute`, chấm số từ `result_summary`, tiêu chí mới `meta_valid`; đã xóa `tests/llm_eval/prompts/analysis.md`.
  `thresholds.yaml` chưa có ngưỡng cho `meta_valid` (người dùng chốt).
- 4.2 Chạy kiểm định vai trò analysis với model thật (`run_eval.py ... --resume`, có thể mất nhiều ngày).
- 4.3 Chạy bài 0a, rồi bài 1 bằng `analysis.loop`.
- Kiểm định report/slide để sau, dùng `--resume`.

## Quyết định đã chốt
- Prompt analysis trong `skills/analysis/` là nguồn duy nhất cho cả bộ kiểm định lẫn agent: kiểm định đo đúng
  prompt sẽ dùng thật.
- Code AI ghi file meta (`result_summary` + `checks`) qua `AGENT_META_PATH`; vòng lặp chấm bằng `validators/`:
  con số và phép kiểm do code tính, không do AI tự khai.
- AI phân loại giả định thành 3 kind của ledger (`metric_definition`, `assumption`, `anomaly_handling`) để ghi
  thẳng bằng `add_decision`.
- Ngày 5 chỉ có xác nhận y/n đơn giản; điểm dừng đầy đủ để Ngày 6.
- Bài 1 chỉ che `CUSTOMER.customer_id` (`config.ANALYSIS_DEFAULT_SENSITIVE`).
- Giữ Groq (`groq_gpt_oss_120b`) trong chuỗi analysis; vì vậy giới hạn prompt nhỏ nhất là 5000 token.

## Giới hạn và việc để sau (Ngày 6 trở đi)
- Prompt lập kế hoạch (`plan.md`) chưa có bảo vệ độ dài (bài 1 đo khoảng 3.600 token, dưới ngân sách 4.500).
- Ước lượng token đang dùng số ký tự / 3,4 (`PROMPT_CHARS_PER_TOKEN`) + 700 cho chỉ dẫn schema.
- Che giá trị chỉ thay chuỗi khớp nguyên văn (giá trị >= 4 ký tự); reason của check custom lấy nguyên từ meta.
- Cache input có thể cũ nếu R2 lỗi kết nối (có cảnh báo `R2_UNREACHABLE`).
- Runner và grader của bộ kiểm định không phải sandbox thật (quét tĩnh lách được bằng ghép chuỗi lúc chạy).
- `run_log` chưa ghi lỗi 403 của runner (`ClientError` khi liệt kê R2 được ném ra trước khi ghi log).
- Hạn mức: Gemini 3.8 Flash 20 lượt/ngày, OpenRouter 50 lượt/ngày (`config/llm.yaml`).
- `Dockerfile` chưa COPY `analysis/` (thêm khi đóng gói).
- `requirements.txt` hiện không có scipy (tóm tắt Ngày 1 có nhắc scipy): chưa xác nhận có cần không.
- Dictionary bài 1, 2 nhắc bảng EXTERNAL_FACTOR, PRICE_CHANGE không có trong dữ liệu.
- `settings.enforce_validation: false` và mọi model `validated: false`: đổi sau khi kiểm định xong.

## Cách bắt đầu phiên mới
1. Đọc `CLAUDE.md` (quy tắc bất biến, lệnh, cấu trúc).
2. Đọc `PROGRESS.md` (đang ở ngày nào, còn gì).
3. Chạy `git log --oneline -10` và `git status` để biết commit gần nhất và thay đổi chưa commit.
