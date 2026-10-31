# PROGRESS

Cập nhật: 31/10/2026. Nguồn: git log, code trong repo, tóm tắt cuối ngày của người dùng.

## Trạng thái theo ngày

| Ngày | Trạng thái | Kết quả |
|---|---|---|
| 1 | Xong | Cấu trúc repo, `config.py` đọc `.env`, token R2 đọc / đọc-ghi tách riêng, `storage/r2.py` (chặn `answers/`), 4 bộ test bài 0a, 0b, 1, 2 trên R2. |
| 2 | Xong | Lớp LLM: `ask_model` tự điều phối chuỗi qua 4 hãng (không Router), cache sqlite, usage log, JSON pydantic, anonymize, task_id, FakeLLM, `ask_one_model`; bộ kiểm định 12/10/10 case. 179 test pass. |
| 3 | Xong | `ledger/` (hồ sơ bài ở `output/<bài>/`), usage log nối vào `run_log.jsonl`, Dockerfile build và chạy được. 238 test pass. |
| 4 | Xong | `profiling/` (quality, dictionary, flags, relationships, data_summary), `validators/`, khoảng hợp lệ trong dictionary; `tests.grade_bai0b` đạt 5/5 lỗi cài sẵn. 476 test pass. |
| 5 | Xong | Runner, skill phân tích, meta_grader, `analysis.loop`, fit_prompt, bộ kiểm định theo skill thật; chốt chuỗi analysis. Bài 0a ĐẠT toàn bộ; bài 1 chạy trọn 4 câu, số liệu đúng, phán đoán BI chưa đạt. 645 test pass. |
| 6 | Xong | Điều phối theo pha, chạy tiếp được, `--fake` tách hồ sơ, điểm dừng xác nhận, decision pending và superseded, cờ chất lượng thành decision, `context_builder`, đồng bộ hồ sơ lên R2, 5 quy tắc chung trong prompt, `tests.grade <bài>`. Bài 2 chạy thật: số tổng hợp khớp, phán đoán BI chưa đạt. 858 test pass. |
| 7 | Xong | Kiểm định report; pha synthesize; `output_path` đủ file bảng; biểu đồ, Word, hồ sơ bài toán, dàn ý slide, `--resynthesize`, `--build-report`. Bài 0a chạy thật: `grade_bai0a` ĐẠT 3/3, Word đủ 7 mục, không còn placeholder trong Word và `slides_outline.json`, mọi insight dẫn về id kết quả. 1239 test pass, commit cuối 2fb62a9. |
| 8 | Đang làm | Đã xong: cột nhạy cảm khai báo trong dictionary, bỏ hằng số cột che mặc định trong `config.py`, profile lưu etag dictionary và dừng mã 3 khi lệch, chưa commit. 1251 test pass. Còn lại xem "Kế hoạch Ngày 8". |

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

Làm trong 2 buổi. Kết quả bài 2 và đánh giá phán đoán BI ở dưới theo tổng kết của người dùng.

Đã xong (858 test pass):
- Điều phối theo pha ở `main.py`: `state.json`, chạy tiếp từ pha đang dở, `--restart`, `--fake` ghi vào `output/_fake/`.
- Điểm dừng xác nhận; decision pending và superseded; cờ chất lượng thành decision theo `source_key`.
- `context_builder.py` ở gốc dự án cho cả understand, plan, step.
- Đồng bộ hồ sơ lên R2 (`storage/sync.py`).
- Chạy bài 2 thật, chấm bằng `tests.grade`.

Thay đổi so với kế hoạch:
- Ngày 6 làm 2 buổi.
- Hồ sơ thêm `state.json`, là file thứ 8.
- Ước lượng token dùng ký tự / 3,4 (đo thật lệch khoảng 1%) thay cho ký tự / 4.
- `context_builder.py` đặt ở gốc dự án.
- 5 quy tắc chung thêm vào prompt TRƯỚC khi chạy bài 2.
- Công cụ chấm tổng quát `tests.grade <bài>`.

Kết quả bài 2:
- Số tổng hợp theo kênh, chiến dịch, `cost_tier` khớp đáp án.
- Phán đoán BI chưa đạt:
  - Không kiểm định thống kê nhưng vẫn ghi mức chắc chắn "Cao".
  - Không phát hiện khoảng 36% touchpoint xảy ra trước ngày đăng ký.
  - Quy tắc 2 và 3 (nhãn so với hành vi, kỳ trọn vẹn) không có tác dụng.
  - AI xếp quyết định BI (cửa sổ quy đơn 30 ngày, ngưỡng ngân sách) vào kind `assumption`, nên chỉ được duyệt gộp.
- Hai lần chạy cùng câu cho cách tính khác nhau: model không ổn định giữa các lần chạy.
- Hồ sơ `bai2` hiện tại được tạo trước khi sửa `--restart` cho decision: 5 kết quả superseded, 5 valid, nhưng 18 decision
  đều confirmed, gộp cả hai lần chạy.

Bài học cần chuyển thành CODE, không thêm lời nhắc vào prompt:
- (a) Phép kiểm cố định "so sánh nhóm phải kèm kiểm định": code đòi kết quả kiểm định trước khi cho ghi mức chắc chắn, có
  xử lý nhiều phép so sánh.
- (b) Profiling thêm kiểm thứ tự thời gian giữa các bảng (sự kiện trước ngày đăng ký) và kỳ dữ liệu trọn vẹn.
- (c) Mọi decision required hỏi riêng từng cái, không duyệt gộp theo kind.
- (d) `tests.grade` bỏ qua file dữ liệu dòng chi tiết khi dò "số gần nhất" (đang gây LỆCH giả).

Chi tiết từng mục:

Mục 1, điều phối theo pha:
- `main.py`: `python main.py <bài> [--sensitive BANG.cot] [--restart] [--yes] [--fake]`, máy trạng thái understand →
  explore → plan → loop → synthesize → done, ghi `state.json` sau mỗi pha (phase, plan, sensitive, updated_at).
  `python -m analysis.loop` chỉ gọi vào luồng này.
- Chạy lại tiếp từ pha đang dở: kế hoạch có trong state thì không gọi lại AI; phase đã qua plan nghĩa là đã chấp nhận;
  câu có trong `results_by_question()` (`question_key`, chỉ kết quả valid) thì bỏ qua; câu dở giải lại từ lần thử 1.
- Pha understand: `skills/analysis/understand.md`, schema `ProblemRestatement`; không đồng ý thì gõ chỉnh sửa, gọi lại AI
  tối đa 3 lần sau lần đầu (`ANALYSIS_UNDERSTAND_MAX_RETRIES`); bản đã xác nhận ở `problem.json`, là đầu vào của pha plan.
- `load_context` chạy trước understand vì đề gửi AI cần tập giá trị che lấy từ profile; pha explore chỉ in tổng quan.
- Pha synthesize lúc đó mới là khung; Ngày 7 mục 1 đã làm thật.
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
- `CUSTOMER.customer_id` bài 1 che bằng thẻ `(PK, SENSITIVE)` trong dictionary (từ Ngày 8, trước đó bằng hằng số trong `config.py`);
  `ORDER.customer_id` che theo quan hệ.

Mục 2 + 3, điểm dừng và decision chờ xác nhận:
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

Mục 4, `context_builder`:
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

Mục 5, đồng bộ hồ sơ lên R2:
- `storage/sync.py`: tải toàn bộ `output/<bài>/` (kể cả `work/_superseded/`, trừ file tạm của Ledger) lên
  `R2_OUTPUT_PREFIX + <bài>/` qua `r2.upload_file`, hàm này dùng đúng token `R2_WRITE_*`. Không xóa gì trên R2.
- Từ chối bằng code, kể cả gọi tay: `_fake` (tên bài hoặc đang ở `fake_output`), tên bài sai, prefix cấu hình sai (rỗng,
  thiếu `/`, trùng `input/`, `answers/`), mọi khóa ngoài prefix hoặc có `answers`.
- Manifest `cache/sync_manifest/<bài>.json` (đường dẫn → sha256), cập nhật sau từng file tải thành công. Manifest hỏng thì
  coi như rỗng.
- Gọi ở: trước mỗi câu hỏi xác nhận (`_with_sync` bọc `ask_user`, bỏ qua câu gõ tiếp), khi xong, khi thoát mọi đường kể cả
  KeyboardInterrupt (giờ bắt được, mã 3) và lỗi ngoài dự kiến. `--fake`, `--no-sync` không gọi. `run_task` gọi trực tiếp
  mặc định tắt, `run_cli` mới bật, để test không có nguy cơ gọi R2.
- Lỗi đồng bộ: một dòng cảnh báo, sự kiện `sync` trong run_log (uploaded, skipped, failed, not_attempted, errors chỉ gồm
  tên lỗi). Lỗi một file thì tải tiếp file khác; mất kết nối thì dừng lượt đó để không chờ timeout từng file.
- `python -m storage.sync <bài> [--dry-run]`: mã 0 xong, 1 có file lỗi, 2 bị từ chối.

Mục 6, chuẩn bị chạy bài 2:
- 5 quy tắc chung được thêm vào prompt TRƯỚC khi chạy bài 2; bài 2 là bài kiểm tra chưa thấy, nên kết quả bài 2 đo được
  các quy tắc này có tác dụng hay không. Quy tắc viết chung, không nhắc tên bài, bảng, cột, con số hay lĩnh vực.
- Kết quả kiểm định analysis Ngày 5 đo trên prompt cũ, chưa chạy lại.
- Chỉ thêm vào prompt: không sửa `meta_grader`, không có phép kiểm bằng code, nên AI bỏ qua quy tắc thì không có gì bắt lại.
- `plan.md`: cột nhãn hoặc trạng thái, kỳ dữ liệu. Bước plan không chạy code, nên chỉ đối chiếu được trên bản tóm tắt.
- `step.md`: so sánh nhóm, ngoại lai. `contract.md`: check custom đo trực tiếp rủi ro, không thay bằng check đếm dòng.
- Prompt plan khi chưa điền biến còn 5.993 ký tự, sát trần 6.000 của `test_prompt_lengths`.
- Kích thước ước lượng bài 2 (cách đo như mục 4, đã che `CUSTOMER.customer_id`, ngân sách 8.100 token): plan ~4.694 thành
  ~4.805, step chưa có kết quả ~4.583 thành ~4.718.
- `python -m tests.grade <bài>` dùng chung cho mọi bài; `python -m tests.grade_bai1` gọi vào đó.
- Từ Ngày 8 bài 2 không cần `--sensitive CUSTOMER.customer_id`: dictionary đã có thẻ `(SENSITIVE)`.

Sửa `--restart` (kiểm bằng FakeLLM và R2 giả; hồ sơ `bai2` thật chưa chạy lại). Lỗi: `--restart` chỉ đổi kết
quả, decision cũ vẫn confirmed nên được dùng lại theo `source_key` (D1, D3 `bai2` không được hỏi lại) và vẫn gửi cho AI.
- `DecisionStatus` thêm `superseded`; hồ sơ cũ vẫn đọc được. `Ledger.active_decisions()` bỏ superseded; `decisions()` vẫn
  trả đủ để đối chiếu.
- `--restart` chuyển mọi decision chưa superseded, kể cả pending, sang superseded (`supersede_decisions`), giữ `text` và
  `confirmed_at`; đóng open_item `PLAN_OPEN_ITEM`, `PENDING_OPEN_ITEM`, open_item khác giữ nguyên.
- Dò `source_key`, `_has_anomaly_decision`, `render_markdown` đọc `active_decisions()`; `pending_required()`, `get_summary`,
  danh sách gửi AI và câu hỏi lọc theo pending/confirmed nên tự bỏ superseded. Decision superseded không xác nhận lại được.
- `tests/grade.py` bỏ decision superseded khi đối chiếu bẫy, để decision của lần làm trước không tính là ĐÃ NÊU.
- Thay hành vi cũ: `--restart` trước đây giữ nguyên decisions và không hỏi lại. 5 test cũ khóa hành vi đó đã sửa theo.

## Ngày 7

Kết quả: bài 0a chạy thật, `python -m tests.grade_bai0a` ĐẠT 3/3. Báo cáo Word đủ 7 mục, không còn placeholder trong Word và
`slides_outline.json`, mọi insight dẫn về id kết quả. 1239 test pass, commit cuối 2fb62a9.

Lệnh mới:
- `python main.py <bài> --resynthesize`: chỉ chạy lại pha tổng hợp (gọi AI), giữ kết quả và các decision khác. Dùng khi không
  ưng kết luận hoặc prompt `synthesize.md` đã đổi.
- `python main.py <bài> --build-report`: dựng lại biểu đồ, Word, hồ sơ, dàn ý từ `insights.json`, không gọi AI. Dùng khi code
  báo cáo đổi hoặc lần dựng trước lỗi.
- `s` ở điểm dừng "Chấp nhận danh sách câu hỏi nhỏ của kế hoạch?": gõ góp ý để AI lập lại kế hoạch.
- Thông báo "Đóng file ... rồi chạy lại": `--restart`, `--resynthesize`, `--build-report` đã dừng trước khi ghi nên chưa đổi gì.
  Đóng file trong Word rồi chạy lại. Đã đóng mà vẫn báo thì còn tiến trình Word ẩn: tắt `WINWORD.EXE` trong Task Manager (tab
  Details), hoặc `Stop-Process -Name WINWORD` (mất phần chưa lưu của mọi file Word đang mở).

Mục 1, pha synthesize (lúc làm kiểm bằng FakeLLM, chưa gọi AI thật):
- `skills/report/`: `synthesize.md`, `schemas.py` (SynthesisOutput, Insight, InsightRecord, SynthesisRecord), `__init__.py`.
- `report/placeholders.py`: `find_placeholders`, `format_value`, `fill`, `validate_synthesis`, `assign_label`. Code điền số,
  định dạng kiểu Việt Nam, không đổi tỷ lệ sang phần trăm.
- `report/synthesize.py`: dựng prompt qua `context_builder` (role report, `prompt_fit`), gọi AI, kiểm tra, gửi lại tối đa
  2 lần kèm danh sách lỗi và câu trả lời trước; vẫn sai thì open_item, dừng mã 3, không ghi `insights.json`. Hết chuỗi: mã 2.
- `main.py`: `_phase_synthesize` in insight theo yêu cầu, hỏi "Chấp nhận kết luận?" (luôn dừng), `--yes` tự xác nhận. `n`:
  bản tổng hợp sang `_superseded/` ở gốc hồ sơ, giữ pha synthesize, chạy lại gọi AI lần nữa. `--restart` cũng chuyển
  `insights.json` sang đó. `insights.json` còn đó (ngắt giữa chừng) thì chạy lại chỉ hỏi lại, không gọi AI.
- Khung `loop.synthesize` đã bỏ. `FakeLLM` nhận phần tử là hàm (`demo_synthesis` đọc id kết quả từ prompt vì id đổi sau
  `--restart`). `loop._short_decisions` tách thành `short_decisions(ledger)` để dùng lại, hành vi không đổi.
- Quyết định khác yêu cầu ban đầu:
  - Danh sách yêu cầu lấy từ `state.plan.requirements` (khớp số `covers`), fallback `restatement.goals`, vì `problem.json`
    không có danh sách yêu cầu.
  - `use_cache=False`: cache trả lại đúng câu cũ nên từ chối hoặc sai hết lần rồi chạy lại sẽ không đổi được gì.
  - `task_id` là `<bài>/report`, tách khỏi model analysis gắn theo `<bài>`.
  - Thêm phép kiểm dấu %: chỉ hợp lệ ngay sau trường có `pct`/`percent`. Không có phép kiểm này thì
    `{{R7.best_channel_conv_rate}}%` ra "0,05%", sai 100 lần.
  - Placeholder trong summary, conclusion, limitations phải trỏ kết quả đã được ít nhất một insight trích dẫn.
  - Insight không có `result_ids` được phép (yêu cầu chưa trả lời được), nhãn luôn hypothesis.
  - Khóa `result_summary` chỉ một cấp: `AgentMeta` không cho dict con và hồ sơ thật đều phẳng.
  - Decision "chấp nhận kết luận" dùng kind `assumption`, `source_key` cố định `accept_conclusion|synthesize`.
- Độ dài prompt (ước lượng, chưa cắt gì, ngân sách 27.000 token, sau khi thêm `tables`): khung 2.515 ký tự ~1.440 token; `bai0a`
  4.063 ký tự ~1.895 token; `bai2` 12.070 ký tự ~4.250 token. `bai2` vẫn dưới 4.500 của groq ở lần gọi đầu; lần gửi lại dài hơn
  vì kèm câu trả lời trước.

Sửa sau lần làm đầu của mục 1:
- Định dạng số: |x| >= 1 tối đa `REPORT_MAX_DECIMALS` chữ số thập phân; 0 < |x| < 1 theo `REPORT_SIG_DIGITS` chữ số có
  nghĩa, để `bai2` R7 in 0,0513 và 0,0519 thay vì đều là 0,05.
- Nhãn verified xét cả chuỗi `depends_on` bắc cầu: kết quả phụ thuộc thiếu, không valid hoặc có check chưa pass thì thành
  hypothesis.

Sửa sau lần chạy thật `bai0a` (AI chỉ thấy result_summary nên yêu cầu trả lời bằng bảng bị viết chung chung):
- `synthesize.md` thêm 2 quy tắc: yêu cầu trả lời bằng bảng thì chỉ rõ "bảng của R..", không khuyến nghị khi không có căn cứ.
  Bỏ cụm "việc nên làm" ở mô tả `conclusion` vì mâu thuẫn với quy tắc sau.
- Prompt có `tables` cho từng kết quả: tên file .csv/.parquet và tên cột, chỉ đọc phần đầu file, không có giá trị dòng.
- Danh sách file lấy từ `output_path` CỘNG `files_created` của mọi lần thử cùng bước trong `run_log`. Lý do: R2 `bai0a` có
  `output_path` chỉ là `work/top_category.json`; lần thử 2 đã ghi `category_brand_product_count.csv`, lần thử 3 ghi y hệt nên
  runner không liệt kê. File không có trên đĩa (đã chuyển đi khi `--restart`) thì bỏ.
- `python main.py <bài> --resynthesize`: chỉ chạy lại pha synthesize, state phải là synthesize hoặc done; pha sớm hơn từ
  chối, mã 3. `Ledger.supersede_decisions(source_key=...)` chỉ đổi decision chấp nhận kết luận.
- Điểm dừng "Chấp nhận danh sách câu hỏi nhỏ": thêm `s` gõ góp ý, tối đa `ANALYSIS_PLAN_MAX_RETRIES` lần. `plan.md` không
  đổi: góp ý vào `user_corrections` nên đi qua `confirmed_understanding`, tính vào trần `CONTEXT_CORRECTIONS_*`, buộc thêm
  cả bước giải. Câu hỏi của kế hoạch cũ gửi kèm một lần để AI biết góp ý nói về gì.
- `state.json` thêm `replaced_plan`, `plan` về null ngay khi có góp ý, nên ngắt giữa chừng rồi chạy lại vẫn lập kế hoạch mới.
  Decision do kế hoạch cũ sinh mà kế hoạch mới không sinh y hệt thì superseded; decision giữ nguyên không hỏi lại.
- `tests/grade_bai0a.py` mục 3: bỏ tên nhóm khỏi danh sách trước khi so mã. `bai0a` không có CSV tổng theo nhóm nên `agent_cat`
  rỗng, phép lọc cũ không bỏ được tên. Vẫn so bằng trên tập mã, thừa mã là RỚT.
- Chưa chạy lại `bai0a` thật sau các sửa này (không gọi AI thật). Quy tắc prompt không có phép kiểm bằng code.

Giới hạn của mục 1:
- Đồng bộ R2 không xóa: `insights.json` cũ trên R2 còn đó tới khi có bản mới ghi đè.
- Hồ sơ `bai2` đang `done`, chưa có `insights.json`, muốn tổng hợp phải `--restart` hoặc sửa tay `state.json`.

Phần A, file bảng của kết quả:
- `RunResult.files_written`: mọi file lần chạy ghi, kể cả ghi lại y hệt (so `mtime` với bản chép, `copytree` giữ `mtime`).
  `files_created` giữ nghĩa cũ (đổi nội dung, dùng để chép về). `output_path` lấy từ `files_written`.
- `report/tables.py`: `result_tables(ledger, result_id)` trả path, name, columns, rows, là cách duy nhất lấy bảng. Phần đọc
  `run_log` nằm trong đó, chỉ để đọc hồ sơ cũ.
- Điểm dừng: nhập sai hoặc góp ý rỗng thì hỏi lại, chỉ n mới dừng; áp cho cả `confirm` (y/n), vì gõ nhầm ở "Chấp nhận kết
  luận?" trước đây làm mất bản tổng hợp. Giữ một ngoại lệ: pha hiểu đề, "Gõ chỉnh sửa (để trống để dừng)" vẫn dừng khi để trống.

Phần B, biểu đồ (`report/charts.py`):
- Insight thêm `table`, `chart`, gốc thêm `column_labels`; `validate_visuals` kiểm, sai thì gửi lại AI. Hai schema để trường
  rộng để lỗi đi qua danh sách lỗi thay vì lỗi schema.
- x (cùng series) phải duy nhất từng dòng: code không tự cộng gộp, vì cộng cột tỷ lệ ra số sai.
- Biểu đồ đường giữ đủ điểm, không lấy top 15: lấy top sẽ phá thứ tự thời gian.
- stacked_bar quá 8 dải thì gộp vào "Khác" màu xám. Nhóm dài hoặc nhiều nhóm thì vẽ cột ngang.

Phần C, D, E, F, G:
- `report/docx_builder.py`, `report/build.py`, `report/slides_outline.py`. Mọi chuỗi đưa vào Word, `ho_so_<bài>.md`,
  `slides_outline.json` đều qua `ctx.mask`: `text_filled` điền giá trị thật nên có thể chứa giá trị cột che.
- Báo cáo dựng SAU khi state là done: lỗi dựng báo cáo không làm mất kết luận đã chấp nhận, sửa rồi `--build-report`.
- `--restart`, `--resynthesize`, trả lời n: `report/` cũ sang `_superseded/report_<thời điểm>/` cùng `insights.json`.
- Cột số có từ year, nam, id, code, ma, key trong tên in nguyên, không ngăn nghìn (`REPORT_PLAIN_NUMBER_HINTS`).
- Tóm tắt cắt ở cuối câu, tối đa `REPORT_SUMMARY_MAX_CHARS` ký tự.
- Dockerfile thêm `COPY templates/`; `tests/test_dockerfile.py` đối chiếu thư mục mã nguồn. Chưa build.
- `validate_synthesis` cấm mọi mã R/Q/I/D kèm số; chú thích bảng và mục 6 của Word giữ mã. Decision "chấp nhận R.. dù có cờ"
  chuyển từ mục 5 sang mục 6 vì chứa mã.
- Trang bìa bỏ "Tên bài"; "Người lập" từ `REPORT_AUTHOR` (tùy chọn), không có thì bỏ dòng.

Sau lần chạy thật `bai0a` (số liệu đúng, đã xem Word):
- `tests/grade_bai0a.py`: dò khóa result_summary ưu tiên khớp chính xác (`numeric_keys`); thêm `product` vào mẫu `max_count`, nếu
  không `max_total_products` không còn được tính là số nhóm lớn nhất.
- Biểu đồ dự phòng (`charts.auto_chart`, `with_auto_charts`): insight có table, không có chart, bảng một chiều, hai tới 20 dòng
  thì tự vẽ bar giảm dần, ghi sự kiện `auto_chart`. Chỉ thêm khi dựng báo cáo, `insights.json` không đổi.
- Word: table có `sort` tùy chọn; không có thì giảm dần theo cột số duy nhất, trừ bảng có cột thời gian và bảng nhiều chiều
  (từ hai cột chữ, mã + tên tính là một): hai loại này giữ thứ tự file. Tối đa 40 dòng. Bảng nhiều chiều có `sort`: xếp nhóm
  (cột nhãn đầu tiên) theo tổng cột số, trong nhóm theo cột số, các dòng cùng nhóm liền nhau; `desc: false` đảo cả hai bậc.
- Word: đoạn insight, nhãn, tiêu đề bảng giữ cùng trang với bảng, hình; mọi dòng bảng không cắt ngang trang.
- `validate_synthesis`: conclusion giống summary trên 80% thì gửi lại AI. Câu "Không có hạn chế đáng kể..." được phép có chữ số
  vì trùng nguyên văn `config.REPORT_NO_LIMITATIONS`.
- Quy tắc tránh từ đánh giá mức độ chỉ nằm trong prompt, không có phép kiểm bằng code.
- `--restart`, `--resynthesize`, `--build-report` thử mở ghi từng file trong `report/` trước mọi thao tác ghi; có file bị khóa thì
  dừng, mã 3, không đổi gì. `discard_insights` đổi tên thay vì `shutil.move` (move chép rồi mới xóa, để lại bản sao dở dang) và
  chuyển `PermissionError` thành `ReportLocked`.
- Tiêu đề biểu đồ (trong ảnh, chú thích Hình n) do `charts.chart_title` sinh từ `column_labels`. `ChartSpec.title` giữ lại cho
  `insights.json` cũ nhưng không hiển thị, không kiểm, prompt không yêu cầu nữa.
- Word không lặp bảng, hình: cùng file và cùng tập cột (bảng), cùng type, file, x, y, series (hình) với insight trước thì chỉ ghi
  "Xem Bảng n và Hình m ở trên.", nêu cái đã có. Dàn ý slide vẫn giữ hình cho từng insight.
- Bar dùng chung `tables.ordered` với bảng Word (dùng đặc tả bảng của cùng insight nếu cùng file), bảng và hình cùng thứ tự; cột
  thời gian giữ thứ tự file. Cắt top nhóm của bar giữ thứ tự đó.
- Word: `limitations` bắt đầu bằng "Không có hạn chế đáng kể" thì in đúng `config.REPORT_NO_LIMITATIONS`; `insights.json` không đổi.
- Tiêu đề biểu đồ: nhãn sau "theo", "và" viết thường chữ đầu, trừ khi từ đầu có chữ hoa hoặc chữ số sau ký tự đầu (SKU, ID, Q1).
  Tên riêng thường (Việt Nam) không nhận ra được nên vẫn bị viết thường.
- Stacked_bar: nhóm xếp theo tổng các dải giảm dần (`tables.grouped`, hàm bảng có `sort` dùng), trục thời gian giữ thứ tự file.

## Quyết định đã chốt
- Chuỗi report (31/10/2026): `gemini_3_5_flash_lite` → `mistral_ministral_14b`, cả hai 9/10 và validated. Bỏ
  `groq_gpt_oss_120b` (10/10) vì `max_input_tokens` 5000 kéo trần ngữ cảnh cả chuỗi xuống 4.500; bỏ
  `openrouter_cohere_north_mini_code` vì rớt `no_typed_numbers` (tự gõ số); bỏ `gemma_4_31b` vì lỗi 500 phía Google, chưa kiểm
  định xong. Groq không đưa lại chuỗi report.
- Slide không gọi AI: `report/slides_outline.py` dựng dàn ý từ `insights.json`.
- AI chỉ viết lời; code dựng Word, vẽ biểu đồ, điền số vào placeholder, gán nhãn verified/hypothesis.
- Định dạng số: |x| >= 1 tối đa `REPORT_MAX_DECIMALS` (2) chữ số thập phân; 0 < |x| < 1 theo `REPORT_SIG_DIGITS` (3) chữ số có nghĩa.
- Thứ tự 7 mục Word: Tóm tắt, Mục tiêu, Kết quả, Kết luận, Dữ liệu và chất lượng, Phương pháp và giả định, Hạn chế.
- Biểu đồ 3 loại: bar, line, stacked_bar; bỏ biểu đồ phân bố.
- Hồ sơ bài toán `ho_so_<bài>.md` là file riêng, không chèn vào Word.
- Prompt analysis trong `skills/analysis/` là nguồn duy nhất cho cả bộ kiểm định lẫn agent: kiểm định đo đúng
  prompt sẽ dùng thật.
- Code AI ghi file meta (`result_summary` + `checks`) qua `AGENT_META_PATH`; vòng lặp chấm bằng `validators/`:
  con số và phép kiểm do code tính, không do AI tự khai.
- AI phân loại giả định thành 3 kind của ledger (`metric_definition`, `assumption`, `anomaly_handling`) để ghi
  thẳng bằng `add_decision`.
- Ngày 5 chỉ có xác nhận y/n đơn giản; điểm dừng đầy đủ để Ngày 6.
- Cột che lấy từ 3 nguồn: thẻ `(SENSITIVE)` trong dictionary, `--sensitive`, tự nhận email/phone. `CUSTOMER.customer_id` của bài 1, 2
  ghi `(PK, SENSITIVE)` trong `input/<bài>/data_dictionary.md` trên R2; `tests/make_bai1_bai2.py` sinh đúng bản đó (`tag_sensitive`),
  `input/Data Dictionary.xlsx` không đổi.
- Profile lưu etag dictionary (`report["dictionary"]["etag"]`). Dùng lại report (`load_context`, `profiling.data_summary`) mà etag khác R2
  hoặc profile cũ chưa có etag thì dừng mã 3, không tự profile lại vì sẽ mất `--set`, `--range`. Sửa bằng
  `python -m profiling.data_summary <bài> --refresh` (lệnh này nhận `--set`, `--range`). `--restart`, `--resynthesize` kiểm trước khi đổi hồ sơ.
- Bỏ Groq (`groq_gpt_oss_120b`) khỏi chuỗi analysis: chưa qua kiểm định, tpm 8000 quá nhỏ,
  trần 5000 token làm bài 1 dừng ở Q4. Giới hạn prompt nhỏ nhất của analysis giờ là 9000 token
  (`context_builder.min_input_tokens("analysis")`, do gemma_4_31b).
- Ngưỡng kiểm định analysis chốt 30/10/2026; no_forbidden, handles_missing_column, assumptions_present chỉ ghi nhận
  (mỗi tiêu chí 1 case), không dùng để loại model.
- Lệch thiết kế 6.7 (tạm thời): giữ `enforce_validation: false`, nên model chưa qua kiểm định vẫn chạy khi đứng sau
  trong chuỗi. Lý do: bật `true` thì analysis chỉ còn 1 model (mất fallback), chuỗi slide rỗng và `ask_model` báo
  `LLMConfigError`. Nội dung chính xác của mục 6.7 nằm trong tài liệu thiết kế ngoài repo (chưa đối chiếu nguyên văn).

## Kế hoạch Ngày 8

Đã thống nhất rút gọn:
1. Chạy end-to-end bài 2 (`--restart`) ra csv, Word, hồ sơ, `slides_outline.json` và đồng bộ R2.
2. Đối chiếu `python -m tests.grade bai2`.
3. Thử lỗi chỉ bằng pytest + `tests.grade_bai0b`.
4. Viết README.
5. Nghiệm thu theo mục 7.2 kế hoạch.
6. Ghi backlog.

Bỏ: chạy trong container (giữ Dockerfile), chạy lại bài 1. Cập nhật tài liệu thiết kế và kế hoạch dời sang sau Ngày 8.

## Backlog

### Báo cáo
- Bảng nhiều chiều không có `sort` lệch thứ tự với hình: bảng giữ thứ tự file, stacked_bar xếp theo tổng (Bảng 2, Hình 2 `bai0a`).
- Tên riêng bị viết thường trong tiêu đề biểu đồ ("Việt Nam" thành "việt Nam"): code chỉ nhận ra mã, viết tắt.
- AI né luật chữ số bằng số viết chữ ("mục sáu"): `validate_synthesis` không chặn được. Word mới chỉ đưa câu "Không có hạn chế
  đáng kể..." về nguyên văn.
- Diễn đạt vụng ở I3 `bai0a`.
- Quy tắc tránh từ đánh giá mức độ chỉ nằm trong prompt, không có phép kiểm bằng code.
- Insight chỉ sâu bằng số có trong `result_summary`: AI thấy tên file, tên cột của bảng, không thấy giá trị dòng.
- `Q<số>` nghĩa quý trong bảng hoặc tên cột: văn bản đã bị chặn (coi là mã nội bộ, kể cả khi nghĩa là quý), AI phải viết bằng lời
  hoặc dùng placeholder.
- Kiểm định report mới là sàng lọc trên prompt Ngày 2, chưa chạy trên prompt synthesize thật.
- Prompt synthesize 2.756/3.000 ký tự (`test_prompt_length`), thêm quy tắc phải rút chỗ khác.

### Phân tích
- Kế hoạch dồn nhiều yêu cầu vào 1 câu hỏi.
- Decision trùng ý (D6, D7 `bai0a`) ở pha giải.
- Model không ổn định giữa các lần chạy: hai lần chạy cùng câu cho cách tính khác nhau.
- Bài học Ngày 6, chuyển thành code thay vì lời nhắc trong prompt:
  - (a) So sánh nhóm phải kèm kiểm định, có xử lý nhiều phép so sánh, trước khi cho ghi mức chắc chắn.
  - (b) Profiling kiểm thứ tự thời gian giữa các bảng và kỳ dữ liệu trọn vẹn.
  - (c) Mọi decision required hỏi riêng từng cái, không duyệt gộp theo kind.
  - (d) `tests.grade` bỏ qua file dữ liệu dòng chi tiết khi dò "số gần nhất".
- `plan.md` sát trần: prompt plan 5.993/6.000 ký tự (`test_prompt_lengths`).
- Chạy lại kiểm định analysis vì prompt đã đổi.
- Thử lại kiểm định gemini_3_8_flash và gemma_4_31b khi Google ổn định; kiểm định đủ 12 case cho groq_gpt_oss_120b.
- `findings.json` không superseded khi `--restart`: phát hiện của kết quả cũ còn nguyên, `tests/grade.py` vẫn đối chiếu với chúng.
- `--restart` có thể ra lại đúng kế hoạch cũ: prompt plan không đổi thì trúng cache của `ask_model`.
- Cờ mới xuất hiện sau khi kế hoạch đã được chấp nhận (profiling lại khi đang ở pha loop) chưa được đăng ký thành decision;
  chỉ đăng ký ở pha plan.
- `tests/grade.py` đối chiếu theo từ khóa: LỆCH, CẦN NGƯỜI XEM và đánh giá bẫy cần người xem lại (hai mục bẫy script chấm ĐÃ NÊU
  thực ra là trùng từ).
- Dictionary bài 1, 2 nhắc bảng EXTERNAL_FACTOR, PRICE_CHANGE không có trong dữ liệu.
- `state.sensitive` chỉ lưu `--sensitive`, không lưu cột che từ dictionary: mã 3 chỉ xảy ra khi `--sensitive` khác state, thẻ dictionary đổi
  giữa chừng thì không dừng.
- Che giá trị chỉ thay chuỗi khớp nguyên văn (giá trị >= 4 ký tự); reason của check custom lấy nguyên từ meta.
- Báo cáo cũ gắn `source_key` theo ngưỡng mặc định vì báo cáo không lưu ngưỡng đã dùng (`--set` lúc profiling).

### Hạ tầng
- `--fake` trên hồ sơ `output/_fake/<bài>/` cũ dừng mã 3 (profile chưa có etag), lệnh `--refresh` trong thông báo không đụng tới `_fake`:
  phải xóa tay `output/_fake/<bài>/data_profile.json`, lần `--fake` sau tự profile lại.
- `test_interrupted_at_conclusion_reasks_without_calling_ai` rớt 1 lần khi chạy pytest song song với profiling file lớn (Q2 thử lại, hết kịch
  bản FakeLLM); chạy riêng 3 lần và chạy toàn bộ lúc máy rảnh đều pass. Tránh chạy pytest cùng lúc với việc nặng.
- Đồng bộ tải cả file tạm `~$*` của Word lên R2: cần bỏ qua.
- Bản sao thừa `_superseded/report_20261031_131229_032292` còn trên R2 (và ở `output/bai0a/`): đồng bộ không xóa, phải xóa tay.
- `test_build_report_does_not_call_ai_and_rebuilds` từng rớt một lần khi có Word ẩn (lần chạy đó chậm gấp 5), chưa rõ nguyên nhân,
  không có log lỗi; chạy riêng và các lần chạy toàn bộ sau đều pass.
- R2 `output/<bài>/work/` còn file cũ của lần chạy trước `--restart`, `insights.json` cũ còn tới khi có bản mới ghi đè (đồng bộ không
  xóa): cần xử lý nếu tải hồ sơ từ R2 về.
- Manifest đồng bộ chỉ ở máy này: máy khác hoặc container mới sẽ tải lại toàn bộ; object bị xóa tay trên R2 không được tải lại cho
  tới khi file đổi.
- Sự kiện `sync` tự làm đổi `run_log.jsonl`, nên mỗi lượt sau luôn tải lại file này, dòng log của lượt cuối chỉ lên R2 ở lần chạy
  sau. Với `--yes` không có câu hỏi nên chỉ đồng bộ khi kết thúc.
- `data_profile.json` chứa dòng mẫu thô, kể cả cột che, và được đồng bộ lên `output/` của bucket: cần bỏ trước khi dùng dữ liệu
  công ty hoặc chia sẻ output.
- Hồ sơ thật `bai0a`, `bai2` tạo trước phần A: `output_path` còn thiếu file (đã bù bằng `run_log` trong `result_tables`).
- Hồ sơ `bai1` tạo trước Ngày 6 không có `state.json` và `question_key`: lần chạy đầu giải lại mọi câu, kết quả cũ vẫn valid (có in
  cảnh báo); nên dùng `--restart`.
- Bài đã `done` thì chạy lại chỉ báo xong, không đối chiếu `--sensitive`.
- `runner.OUTPUT_DIR` và `quality.OUTPUT_DIR` đang đổi tạm biến toàn cục cho `--fake`; nên truyền tham số.
- Runner và grader của bộ kiểm định không phải sandbox thật (quét tĩnh lách được bằng ghép chuỗi lúc chạy).
- Open_item không tự đóng hết.
- Mức thận trọng chỉ có `chuan`.
- `enforce_validation` còn false. Bật theo vai trò cần sửa `llm/client.py`: hiện cờ này áp cho mọi vai trò cùng lúc.
- Chuỗi `slide` trong `config/llm.yaml` còn đó, chưa kiểm định; slide hiện không gọi AI.
- Ước lượng token đang dùng số ký tự / 3,4 (`PROMPT_CHARS_PER_TOKEN`) + 700 cho chỉ dẫn schema.
- Cache input có thể cũ nếu R2 lỗi kết nối (có cảnh báo `R2_UNREACHABLE`).
- Hạn mức: Gemini 3.8 Flash 20 lượt/ngày, OpenRouter 50 lượt/ngày (`config/llm.yaml`).
- `requirements.txt` không có scipy (tóm tắt Ngày 1 có nhắc scipy): chưa xác nhận có cần không.
- `run_eval` chỉ lưu `finish_reason` của lần gọi cuối (llm/ không trả theo từng lần thử lại JSON).

## Cách bắt đầu phiên mới
1. Đọc `CLAUDE.md` (quy tắc bất biến, lệnh, cấu trúc).
2. Đọc `PROGRESS.md` (đang ở ngày nào, còn gì).
3. Chạy `git log --oneline -10` và `git status` để biết commit gần nhất và thay đổi chưa commit.
