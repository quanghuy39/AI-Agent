# PROGRESS

Cập nhật: 30/10/2026. Nguồn: git log, code trong repo, tóm tắt cuối ngày của người dùng.

## Trạng thái theo ngày

| Ngày | Trạng thái | Kết quả |
|---|---|---|
| 1 | Xong | Cấu trúc repo, `config.py` đọc `.env`, token R2 đọc / đọc-ghi tách riêng, `storage/r2.py` (chặn `answers/`), 4 bộ test bài 0a, 0b, 1, 2 trên R2. |
| 2 | Xong | Lớp LLM: `ask_model` tự điều phối chuỗi qua 4 hãng (không Router), cache sqlite, usage log, JSON pydantic, anonymize, task_id, FakeLLM, `ask_one_model`; bộ kiểm định 12/10/10 case. 179 test pass. |
| 3 | Xong | `ledger/` (hồ sơ bài ở `output/<bài>/`), usage log nối vào `run_log.jsonl`, Dockerfile build và chạy được. 238 test pass. |
| 4 | Xong | `profiling/` (quality, dictionary, flags, relationships, data_summary), `validators/`, khoảng hợp lệ trong dictionary; `tests.grade_bai0b` đạt 5/5 lỗi cài sẵn. 476 test pass. |
| 5 | Xong | Runner, skill phân tích, meta_grader, `analysis.loop`, fit_prompt, bộ kiểm định theo skill thật; chốt chuỗi analysis. Bài 0a ĐẠT toàn bộ; bài 1 chạy trọn 4 câu, số liệu đúng, phán đoán BI chưa đạt. 645 test pass. |
| 6 | Đang làm | Mục 1, 2, 3, 4 xong (chưa commit): điều phối theo pha, chạy tiếp được, `--fake` tách hồ sơ, điểm dừng xác nhận, decision chờ xác nhận, cờ chất lượng thành decision, `context_builder`. 783 test pass. Còn lại theo ghi chú: mục 5 (đồng bộ hồ sơ lên R2); thêm các bài học ở mục "Bài học chung cho Ngày 6". Phạm vi đầy đủ: chưa xác nhận. |
| 7 | Chưa | Chưa xác nhận (thư mục `report/`, `slides/` mới có `__init__.py`). |
| 8 | Chưa | Chưa xác nhận (ghi chú: `LLM_CONFIG_PATH` kiểu Windows không dùng được trong container). |

## Ngày 5

Đã xong (commit d58c0bb..ef99687, riêng phép kiểm join và `tests/grade_bai1.py` chưa commit):
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
  | value_correct (0,6) | 9/10 | 8/10 |
  | no_invented_columns (0,9) | 12/12 | 12/12 |
  | meta_valid (0,8) | 10/12 (83%, sát ngưỡng) | 9/12 (75%, rớt) |
  | Case đạt toàn bộ | 9/12 | 8/12 |
  | Qua ngưỡng | CÓ | KHÔNG |

  Không qua hoặc chưa đủ dữ liệu: groq_gpt_oss_120b (mới 2 case), openrouter_cohere_north_mini_code (rớt json_valid,
  bỏ khỏi analysis), gemini_3_8_flash và gemma_4_31b (lỗi 503/408 phía Google kể cả prompt ngắn).
- Chuỗi analysis đã chốt (`config/llm.yaml`): gemini_3_5_flash_lite (validated: true) → mistral_codestral →
  gemini_3_8_flash → gemma_4_31b (đều validated: false). `enforce_validation: false`. Đã bỏ groq_gpt_oss_120b và cohere.
- 4.3 Chạy bài thật bằng `analysis.loop`:
  - Bài 0a: ĐẠT toàn bộ (`python -m tests.grade_bai0a`).
  - Bài 1: chạy trọn 4 câu (R1 đến R4), số liệu đúng. `python -m tests.grade_bai1` (kết quả của lần chạy TRƯỚC khi có
    phép kiểm join mới, chưa chấm lại lần chạy sau): 99 mốc KHỚP trên 204 mốc, không số
    nào sai. 35 mốc LỆCH là chỉ số agent không tính, script ghép nhầm với số khác (đã đọc từng dòng); 53 mốc KHÔNG TÌM
    THẤY và 17 mốc CẦN NGƯỜI XEM còn lại là chỉ số chưa tính hoặc trùng giá trị.
  - Bài 1, phán đoán BI chưa đạt: bỏ sót bẫy nhãn churn không khớp hành vi mua; kết luận nhóm ưu tiên dựa trên chênh
    lệch không có ý nghĩa (26,4% đến 28,4%); thiếu mức chắc chắn; không hỏi về khách nhiều đơn bất thường và nơi mua.
- Phép kiểm mới trong `analysis/meta_grader.py`: khai báo join không khớp phép nối thật thành lỗi cấu trúc (join dự báo
  nhân bản dòng nhưng có row_count giữ nguyên số dòng bảng trái), để vòng lặp bắt AI sửa. Lý do: R3 và R4 bài 1 khai
  ORDER thô trong khi nối với bảng đã gộp, và quy tắc trong contract không có tác dụng vì không có gì bắt buộc. Đã chạy
  trên bài 1 thật: lần chạy sau khi thêm phép kiểm, R1 đến R4 đều 0 flag; R3 và R4 có "lần 1 chưa đạt" rồi đạt, trong khi
  lần trước R4 còn cờ join. Nhiều khả năng do phép kiểm mới; chưa đối chiếu run_log.

## Ngày 6

Mục 1, điều phối theo pha (chưa commit, mới kiểm bằng FakeLLM và R2 giả, chưa chạy bài thật):
- `main.py`: `python main.py <bài> [--sensitive BANG.cot] [--restart] [--yes] [--fake]`, máy trạng thái understand →
  explore → plan → loop → synthesize → done, ghi `state.json` sau mỗi pha (phase, plan, sensitive, updated_at).
  `python -m analysis.loop` chỉ gọi vào luồng này.
- Chạy lại tiếp từ pha đang dở: kế hoạch có trong state thì không gọi lại AI; phase đã qua plan nghĩa là đã chấp nhận;
  câu có trong `results_by_question()` (`question_key`, chỉ kết quả valid) thì bỏ qua; câu dở giải lại từ lần thử 1.
- Pha understand: `skills/analysis/understand.md`, schema `ProblemRestatement`; không đồng ý thì gõ chỉnh sửa, gọi lại AI
  tối đa 3 lần sau lần đầu (`ANALYSIS_UNDERSTAND_MAX_RETRIES`); bản đã xác nhận ở `problem.json`, là đầu vào của pha plan.
- `load_context` chạy trước understand vì đề gửi AI cần tập giá trị che lấy từ profile; pha explore chỉ in tổng quan.
- Pha synthesize mới là khung (bảng kết quả + một xác nhận), TODO Ngày 7.
- `--sensitive`: chốt vào state ở lần chạy đầu (gồm cả mặc định của bài), chạy tiếp dùng state, truyền khác thì dừng và
  gợi ý `--restart`; `--restart` không kèm `--sensitive` thì giữ danh sách cũ, không âm thầm bỏ che.
- `--restart`: kết quả valid thành superseded, `output_path` trỏ chỗ mới, toàn bộ file `work/` sang
  `work/_superseded/<thời điểm>/`. Chuyển hết thay vì theo `output_path` vì runner chỉ ghi file có nội dung đổi, nên file
  của lần thử hỏng rồi ghi lại y hệt không có trong `output_path`. Runner không chép `_superseded/` vào `AGENT_OUTPUT_DIR`.
- `previous_results` và bảng kết quả in ra chỉ gồm kết quả valid; danh sách file bỏ `_superseded/`.
- Open_item: không thêm trùng mục đang mở; mục "hết hạn mức" tự đóng khi ghi được một kết quả sau đó.
- Mã thoát gom ở `exit_code_for`; bắt thêm `SensitiveLeakError` (mã 3) và lỗi R2 trên toàn luồng (mã 1, chỉ in tên lỗi).
  Mọi model đều sai JSON/schema (`LLMChainExhausted.json_invalid_only`) báo là sai schema, mã 1, không ghi open_item; chỉ
  hết chuỗi vì lý do khác mới báo hết hạn mức. Trước đó mọi `LLMChainExhausted` đều bị báo là hết hạn mức, kể cả khi chạy thật.
- `--fake`: hồ sơ ghi vào `output/_fake/<bài>/` (`main.fake_output`, gồm cả state.json, work/, code/), in dòng "Chế độ giả
  lập, hồ sơ ở output/_fake/<bài>" đầu lần chạy; hồ sơ thật không bị đụng tới. `output/_fake/` không được đồng bộ lên R2
  (hàm sync làm ở mục 5, ghi chú ở `config.FAKE_OUTPUT_SUBDIR`).
- `demo_fake` trả lời theo loại prompt (`FakeLLM(by_schema=...)`), không theo thứ tự, nên chạy tiếp ở pha nào cũng đúng.
  Lỗi cũ: chạy tiếp bị lệch kịch bản, câu trả lời sai schema rồi báo nhầm là hết hạn mức.
- `Dockerfile` thêm `COPY analysis/`. Chưa build được (Docker Desktop tắt); đã kiểm bằng cách chép đúng các mục COPY sang
  thư mục trống rồi import và chạy `main.py --help`.
- Dictionary bài 1 (bản trong `cache/input/bai1/`) không có thẻ `(SENSITIVE)`: `CUSTOMER.customer_id` chỉ có `(PK)`. Cột
  này được che nhờ `config.ANALYSIS_DEFAULT_SENSITIVE`; `ORDER.customer_id` che theo quan hệ.

Mục 2 + 3, điểm dừng và decision chờ xác nhận (chưa commit, kiểm bằng FakeLLM, R2 giả và `main.py --fake` trên `bai0a`;
chưa chạy với AI thật):
- Giả định của kế hoạch, khoảng hợp lệ và cờ mức flag được ghi `pending` vào `decisions.json` TRƯỚC khi hỏi; `_add_confirmed`
  đã tách thành `_add_pending` và `_confirm_decision`. `pending_required()` còn mục thì không vào pha loop
  (`require_confirmed`); chạy tiếp chỉ hỏi phần còn pending, kể cả decision còn lại khi đang ở pha loop.
- Thứ tự hỏi ở pha plan: định nghĩa chỉ số → xử lý bất thường → giả định khác và khoảng hợp lệ → danh sách câu hỏi nhỏ.
  `metric_definition` và `anomaly_handling` hỏi từng cái (y / s gõ nội dung khác / n dừng); `assumption` và khoảng hợp lệ
  in danh sách đánh số (y chấp nhận hết, gõ số để sửa, n dừng).
- Giả định mới của bước giải cũng đi qua pending và cùng cách hỏi.
- Mỗi cờ có `source_key` = `code|bảng|cột|liên quan`, không chứa số đếm, duy nhất kể cả `join_right_*` và
  `relationship_rejected`. Decision có `source_key` tùy chọn; cờ flag thành decision `anomaly_handling` pending, required,
  không tạo lại khi chạy lại profiling. Giả định AI viết có `source_key` băm từ nội dung gốc, để chọn s rồi chạy lại không
  sinh lại bản gốc. Cờ info không tạo decision theo từng cờ, chỉ in ở điểm dừng xử lý bất thường.
- Điểm dừng xử lý bất thường luôn dừng khi có lưu ý: không có decision `anomaly_handling` (0 cờ flag, AI không đề xuất) mà
  có cờ info thì hỏi một câu "Chấp nhận giữ nguyên dữ liệu với các lưu ý trên?" (y/s/n), lưu một decision required với
  `source_key` cố định `keep_data|info_flags`, chạy lại không trùng. Decision lưu câu tường thuật vì được gửi cho AI. Decision
  "chấp nhận R.. dù có cờ" không tính là đã qua điểm dừng này. Không có cờ nào: in "Không có bất thường", không hỏi.
- Đề xuất của AI cho đúng một cờ: bản tóm tắt in `[key: ...]` cạnh cờ mức flag, kế hoạch có `flag_key`
  (`PlanAssumption`), đề xuất nằm trong decision của cờ thay vì thành decision riêng. Không có đề xuất thì mặc định
  "(chưa có đề xuất của AI) giữ nguyên, không xử lý riêng".
- Báo cáo `data_profile.json` lưu trước khi có `source_key` được gắn khóa lúc nạp (`quality.ensure_flag_keys`, sinh lại cờ từ
  báo cáo, không đọc dữ liệu, chỉ trong bộ nhớ).
- `model_switched`: vòng lặp giữ `LLMResult`; đổi model → check `model_switched` mức flag (reason "model cũ → model mới")
  trên kết quả kế tiếp, đi qua điểm dừng có cờ. Trúng cache không tính là đổi model (và không ghi đè model đang gắn).
  `state.json` có `model`; chạy tiếp ở tiến trình mới khôi phục qua `llm.set_task_model`, nên model cũ cũng được thử trước.
  Đổi model mà chưa có kết quả mang cờ thì state giữ model cũ, để dừng giữa chừng vẫn phát hiện được. `llm.fake.FromModel`
  giả lập đổi model và trúng cache.
- "Vẫn tiếp tục?": ghi decision "chấp nhận <R id> dù có cờ <tên>" pending trước khi hỏi; y → confirmed; n → dừng, chạy
  lại sẽ hỏi lại thay vì đi tiếp.
- `CAUTION_LEVEL` khác `chuan`: in cảnh báo chưa hỗ trợ. `--yes`: in cảnh báo tự xác nhận mọi decision.
- Open_item "kế hoạch chưa được chấp nhận" (O1 của `bai0a`) tự đóng khi kế hoạch được chấp nhận hoặc khi qua được cổng
  `require_confirmed` ở pha loop.
- Số cờ theo hồ sơ hiện có (flag/info): bai0a 0/0, bai0b 7/2, bai1 0/3, bai2 0/6. Bài 1, 2 đi qua câu hỏi giữ nguyên dữ
  liệu; bai0a in "Không có bất thường"; đường cờ flag thành decision chưa có bài thật nào ngoài bai0b để hỏi.

Mục 4, `context_builder` (chưa commit, kiểm bằng FakeLLM và R2 giả; chưa chạy với AI thật):
- `context_builder.py` ở gốc dự án: chuyển `fit_prompt`, `estimate_tokens`, `min_input_tokens` từ `analysis/loop.py` sang,
  tổng quát theo role. Ngân sách = `max_input_tokens` nhỏ nhất của chuỗi role, trừ biên 10%: analysis 8.100, report và
  slide 4.500 token. Giữ ước lượng ký tự / 3,4 + 700 (token thật bài 1 cao hơn ước lượng khoảng 1%).
- Cả 3 prompt đi qua `build_prompt`; `PROMPT_CUTS` khai báo thứ tự cắt:
  - understand: previous_restatement → user_corrections.
  - plan: confirmed_understanding → data_summary.
  - step: previous_results → error_feedback → confirmed_decisions → confirmed_understanding → data_summary.
  Đề gốc, câu hỏi hiện tại, contract không cắt. Bậc data_summary gọi lại `build_data_summary` với 6.000, 4.500, 3.000,
  2.000 ký tự (`CONTEXT_SUMMARY_MAX_CHARS`), vẫn đi qua mức cắt và lưới an toàn che giá trị.
- Trần: `user_corrections` 1.500 ký tự (400 khi cắt), giữ chỉnh sửa mới nhất; `_short_previous` 8 kết quả mới nhất cộng
  kết quả câu hiện tại phụ thuộc, 20 file mới nhất; `_short_decisions` 15 decision, ưu tiên `metric_definition` rồi
  `anomaly_handling`, decision "chấp nhận R.. dù có cờ" bị bỏ trước. `problem.json` vẫn giữ đủ.
- Vẫn vượt sau mọi bậc: open_item chống trùng (text không còn số token), dừng mã 3, không gọi AI. Trước đó chỉ `step` có
  bảo vệ này.
- `step.md` nhận `confirmed_understanding` gồm `user_corrections`; trước đó chỉnh sửa của người dùng chỉ đến pha plan.
  Bộ kiểm định truyền "(none)" cho biến này.
- `max_input_tokens` vào `REQUIRED_MODEL_FIELDS`: thiếu thì báo lỗi khi nạp `llm.yaml`.
- `run_log`: sự kiện `prompt_fit` ở mọi prompt, `extra` gồm role, prompt, limit, budget, số ký tự và token trước/sau, cut
  (mức cắt đã dùng, vd. `data_summary:4500`).
- Kích thước ước lượng hiện tại (token, chưa cắt gì; bản diễn giải giả định bằng nửa độ dài đề vì chưa có hồ sơ thật):

  | Prompt | bai1 | bai2 |
  |---|---|---|
  | understand | ~1.155 | ~1.154 |
  | plan | ~4.238 | ~4.670 |
  | step, chưa có kết quả | ~4.128 | ~4.560 |
  | step, đủ hồ sơ hiện có | ~4.753 | ~4.568 (bai2 chưa có kết quả) |

## Bài học chung cho Ngày 6
Không viết riêng cho bài 1; đo trên bài 2.
- So sánh giữa các nhóm phải nêu độ lớn khác biệt và mức chắc chắn.
- Kiểm tra định nghĩa cột nhãn trước khi dùng.
- Kiểm tra kỳ dữ liệu có trọn vẹn.
- Xem xét ngoại lai.
- Check custom phải kiểm đúng rủi ro đã nêu.

## Quyết định đã chốt
- Prompt analysis trong `skills/analysis/` là nguồn duy nhất cho cả bộ kiểm định lẫn agent: kiểm định đo đúng
  prompt sẽ dùng thật.
- Code AI ghi file meta (`result_summary` + `checks`) qua `AGENT_META_PATH`; vòng lặp chấm bằng `validators/`:
  con số và phép kiểm do code tính, không do AI tự khai.
- AI phân loại giả định thành 3 kind của ledger (`metric_definition`, `assumption`, `anomaly_handling`) để ghi
  thẳng bằng `add_decision`.
- Ngày 5 chỉ có xác nhận y/n đơn giản; điểm dừng đầy đủ để Ngày 6.
- Bài 1 chỉ che `CUSTOMER.customer_id` (`config.ANALYSIS_DEFAULT_SENSITIVE`).
- Bỏ Groq (`groq_gpt_oss_120b`) khỏi chuỗi analysis, vẫn giữ ở report/slide: chưa qua kiểm định, tpm 8000 quá nhỏ,
  trần 5000 token làm bài 1 dừng ở Q4. Giới hạn prompt nhỏ nhất của analysis giờ là 9000 token
  (`context_builder.min_input_tokens("analysis")`, do gemma_4_31b).
- Ngưỡng kiểm định analysis chốt 30/10/2026; no_forbidden, handles_missing_column, assumptions_present chỉ ghi nhận
  (mỗi tiêu chí 1 case), không dùng để loại model.
- Lệch thiết kế 6.7 (tạm thời): giữ `enforce_validation: false`, nên model chưa qua kiểm định vẫn chạy khi đứng sau
  trong chuỗi. Lý do: bật `true` thì chuỗi report/slide rỗng (chưa model nào validated) và `ask_model` báo
  `LLMConfigError`. Nội dung chính xác của mục 6.7 nằm trong tài liệu thiết kế ngoài repo (chưa đối chiếu nguyên văn).

## Giới hạn và việc để sau (Ngày 6 trở đi)
- Prompt report/slide chưa viết (Ngày 7). Trần 5.000 token của chuỗi report/slide (ngân sách 4.500) nhỏ hơn prompt cỡ
  `step` hiện nay, nên khi dùng lại ngữ cảnh này sẽ phải cắt.
- Kết quả kiểm định Ngày 5 đo trên `step.md` trước khi thêm mục bản diễn giải; prompt nay dài thêm một mục ngắn.
- `tests/grade_bai1.py` đối chiếu theo từ khóa: LỆCH, CẦN NGƯỜI XEM và đánh giá bẫy cần người xem lại (hai mục bẫy
  script chấm ĐÃ NÊU thực ra là trùng từ).
- Ước lượng token đang dùng số ký tự / 3,4 (`PROMPT_CHARS_PER_TOKEN`) + 700 cho chỉ dẫn schema.
- Che giá trị chỉ thay chuỗi khớp nguyên văn (giá trị >= 4 ký tự); reason của check custom lấy nguyên từ meta.
- Cache input có thể cũ nếu R2 lỗi kết nối (có cảnh báo `R2_UNREACHABLE`).
- Runner và grader của bộ kiểm định không phải sandbox thật (quét tĩnh lách được bằng ghép chuỗi lúc chạy).
- `--restart` có thể ra lại đúng kế hoạch cũ: prompt plan không đổi thì trúng cache của `ask_model`; từ chối danh sách
  câu hỏi nhỏ chưa có chỗ góp ý.
- Cờ mới xuất hiện sau khi kế hoạch đã được chấp nhận (profiling lại khi đang ở pha loop) chưa được đăng ký thành decision;
  chỉ đăng ký ở pha plan.
- Báo cáo cũ gắn `source_key` theo ngưỡng mặc định vì báo cáo không lưu ngưỡng đã dùng (`--set` lúc profiling).
- Sau `--restart`, decisions cũ (giữ theo quyết định) vẫn được gửi cho AI trong `confirmed_decisions`.
- Hồ sơ `bai1` tạo trước Ngày 6 không có `state.json` và `question_key`: lần chạy đầu giải lại mọi câu, kết quả cũ vẫn
  valid (có in cảnh báo); nên dùng `--restart`.
- LƯU Ý hồ sơ thật `bai0a`: `state.json` đang chứa kế hoạch demo ở phase `loop` (`problem.json` cũng là bản demo, tạo từ
  `--fake` trước khi có `output/_fake/`), kèm open_item O2 "hết hạn mức" sinh ra từ báo sai. Lần chạy thật đầu tiên phải
  dùng `python main.py bai0a --restart`.
- Bài đã `done` thì chạy lại chỉ báo xong, không đối chiếu `--sensitive`.
- Hạn mức: Gemini 3.8 Flash 20 lượt/ngày, OpenRouter 50 lượt/ngày (`config/llm.yaml`).
- Chưa `docker build` lại sau khi thêm `COPY analysis/`.
- `requirements.txt` hiện không có scipy (tóm tắt Ngày 1 có nhắc scipy): chưa xác nhận có cần không.
- Dictionary bài 1, 2 nhắc bảng EXTERNAL_FACTOR, PRICE_CHANGE không có trong dữ liệu.
- Kiểm định report/slide (dùng `--resume`), rồi đánh `validated: true` cho model qua ngưỡng.
- `enforce_validation` còn false. Bật enforce theo vai trò (Ngày 7): hiện cờ này áp cho mọi vai trò cùng lúc, cần sửa
  `llm/client.py`.
- Thử lại kiểm định gemini_3_8_flash và gemma_4_31b khi Google ổn định; kiểm định đủ 12 case cho groq_gpt_oss_120b.
- `run_eval` chỉ lưu `finish_reason` của lần gọi cuối (llm/ không trả theo từng lần thử lại JSON).

## Cách bắt đầu phiên mới
1. Đọc `CLAUDE.md` (quy tắc bất biến, lệnh, cấu trúc).
2. Đọc `PROGRESS.md` (đang ở ngày nào, còn gì).
3. Chạy `git log --oneline -10` và `git status` để biết commit gần nhất và thay đổi chưa commit.
