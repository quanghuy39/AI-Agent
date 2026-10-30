# PROGRESS

Cập nhật: 30/10/2026. Nguồn: git log, code trong repo, tóm tắt cuối ngày của người dùng.

## Trạng thái theo ngày

| Ngày | Trạng thái | Kết quả |
|---|---|---|
| 1 | Xong | Cấu trúc repo, `config.py` đọc `.env`, token R2 đọc / đọc-ghi tách riêng, `storage/r2.py` (chặn `answers/`), 4 bộ test bài 0a, 0b, 1, 2 trên R2. |
| 2 | Xong | Lớp LLM: `ask_model` tự điều phối chuỗi qua 4 hãng (không Router), cache sqlite, usage log, JSON pydantic, anonymize, task_id, FakeLLM, `ask_one_model`; bộ kiểm định 12/10/10 case. 179 test pass. |
| 3 | Xong | `ledger/` (hồ sơ bài ở `output/<bài>/`), usage log nối vào `run_log.jsonl`, Dockerfile build và chạy được. 238 test pass. |
| 4 | Xong | `profiling/` (quality, dictionary, flags, relationships, data_summary), `validators/`, khoảng hợp lệ trong dictionary; `tests.grade_bai0b` đạt 5/5 lỗi cài sẵn. 476 test pass. |
| 5 | Đang làm | Runner, skill phân tích, meta_grader, `analysis.loop`, fit_prompt, bộ kiểm định theo skill thật (4.1) đã commit; kiểm định analysis (4.2) xong, chốt chuỗi analysis. 627 test pass. Còn 4.3. |
| 6 | Chưa | Phạm vi theo ghi chú: điểm dừng đầy đủ (dựa `pending_required()`), đồng bộ hồ sơ lên R2, quality_flags thành mục chờ xác nhận. Phạm vi đầy đủ: chưa xác nhận. |
| 7 | Chưa | Chưa xác nhận (thư mục `report/`, `slides/` mới có `__init__.py`). |
| 8 | Chưa | Chưa xác nhận (ghi chú: `LLM_CONFIG_PATH` kiểu Windows không dùng được trong container; `main.py` còn rỗng). |

## Ngày 5

Đã xong (commit d58c0bb..ffb3489):
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

- 4.1 Bộ kiểm định theo skill thật: prompt analysis lấy từ `render_prompt("step")` + data summary của dữ liệu giả,
  schema AnalysisStep, chạy code bằng hàm lõi `runner.execute`, chấm số từ `result_summary`, tiêu chí `meta_valid`;
  dòng kết quả lưu cấu trúc meta, `finish_reason` (lần gọi cuối), ngữ cảnh SyntaxError (đã che chuỗi).
  Sửa sau lần chạy đầu: contract (ví dụ 2 check custom, không f-string, ép kiểu numpy), bộ chấm cột chỉ tính trên
  DataFrame xác định được, câu hỏi A04 rõ hơn.
- 4.2 Kiểm định analysis (30/10/2026, `tests/llm_eval/results/2026-10-30_*_analysis.jsonl`; ngưỡng
  `thresholds.yaml` đã chốt 30/10/2026; mỗi case lấy lần chạy cuối):

  | Tiêu chí (ngưỡng) | gemini_3_5_flash_lite | mistral_codestral |
  |---|---|---|
  | json_valid (0,9) | 12/12 | 12/12 |
  | code_runs (0,8) | 11/12 | 10/12 |
  | value_correct (0,6) | 8/10 | 8/10 |
  | no_invented_columns (0,9) | 12/12 | 12/12 |
  | meta_valid (0,8) | 11/12 | 9/12 (75%, rớt) |
  | Case đạt toàn bộ | 9/12 | 8/12 |
  | Qua ngưỡng | CÓ | KHÔNG |

  Không qua hoặc chưa đủ dữ liệu: groq_gpt_oss_120b (mới 2 case), openrouter_cohere_north_mini_code (rớt json_valid,
  bỏ khỏi analysis), gemini_3_8_flash và gemma_4_31b (lỗi 503/408 phía Google kể cả prompt ngắn).
- Chuỗi analysis đã chốt (`config/llm.yaml`): gemini_3_5_flash_lite (validated: true) → mistral_codestral → groq_gpt_oss_120b
  → gemini_3_8_flash → gemma_4_31b (đều validated: false). `enforce_validation: false`.

Còn lại:
- 4.3 Chạy bài 0a, rồi bài 1 bằng `analysis.loop`.

## Quyết định đã chốt
- Prompt analysis trong `skills/analysis/` là nguồn duy nhất cho cả bộ kiểm định lẫn agent: kiểm định đo đúng
  prompt sẽ dùng thật.
- Code AI ghi file meta (`result_summary` + `checks`) qua `AGENT_META_PATH`; vòng lặp chấm bằng `validators/`:
  con số và phép kiểm do code tính, không do AI tự khai.
- AI phân loại giả định thành 3 kind của ledger (`metric_definition`, `assumption`, `anomaly_handling`) để ghi
  thẳng bằng `add_decision`.
- Ngày 5 chỉ có xác nhận y/n đơn giản; điểm dừng đầy đủ để Ngày 6.
- Bài 1 chỉ che `CUSTOMER.customer_id` (`config.ANALYSIS_DEFAULT_SENSITIVE`).
- Giữ Groq (`groq_gpt_oss_120b`) trong chuỗi analysis; vì vậy giới hạn prompt nhỏ nhất là 5000 token
  (`analysis.loop.min_input_tokens()` = 5000).
- Ngưỡng kiểm định analysis chốt 30/10/2026; no_forbidden, handles_missing_column, assumptions_present chỉ ghi nhận
  (mỗi tiêu chí 1 case), không dùng để loại model.
- Lệch thiết kế 6.7 (tạm thời): giữ `enforce_validation: false`, nên model chưa qua kiểm định vẫn chạy khi đứng sau
  trong chuỗi. Lý do: bật `true` thì chuỗi report/slide rỗng (chưa model nào validated) và `ask_model` báo
  `LLMConfigError`. Nội dung chính xác của mục 6.7 nằm trong tài liệu thiết kế ngoài repo (chưa đối chiếu nguyên văn).

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
- Kiểm định report/slide (dùng `--resume`), rồi đánh `validated: true` cho model qua ngưỡng.
- Bật enforce theo vai trò (Ngày 7): hiện `enforce_validation` áp cho mọi vai trò cùng lúc, cần sửa `llm/client.py`.
- Thử lại kiểm định gemini_3_8_flash và gemma_4_31b khi Google ổn định; kiểm định đủ 12 case cho groq_gpt_oss_120b.
- `run_eval` chỉ lưu `finish_reason` của lần gọi cuối (llm/ không trả theo từng lần thử lại JSON).

## Cách bắt đầu phiên mới
1. Đọc `CLAUDE.md` (quy tắc bất biến, lệnh, cấu trúc).
2. Đọc `PROGRESS.md` (đang ở ngày nào, còn gì).
3. Chạy `git log --oneline -10` và `git status` để biết commit gần nhất và thay đổi chưa commit.
