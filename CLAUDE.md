# CLAUDE.md

## Dự án
AI agent phân tích dữ liệu: một agent, nhiều skill (hiện có `skills/analysis`, `skills/report`). Chạy local; dữ liệu đề bài nằm trên
Cloudflare R2 (bucket đọc qua `storage/r2.py`, có cache ở `cache/`). Chủ dự án mới học lập trình, làm theo kiểu
vibe code với Claude Code trên Windows 11, VSCode, PowerShell; Python 3.12 trong `.venv`.

Trạng thái từng ngày: xem `PROGRESS.md`.

## Cấu trúc thư mục
- `config.py` – đọc `.env`, mọi hằng số/ngưỡng (profiling, validators, summary, runner, vòng lặp, prompt).
- `config/llm.yaml` – model, hạn mức, chuỗi fallback theo vai trò (analysis/report/slide), settings.
- `llm/` – lớp gọi AI: `client.py` (ask_model, ask_one_model; nơi duy nhất import litellm), `cache.py`, `usage.py`,
  `structured.py` (JSON + pydantic), `anonymize.py`, `fake.py` (FakeLLM), `errors.py`.
- `storage/r2.py` – list/đọc/ghi R2, `download_with_cache` (so etag), `read_table` (csv/parquet), chặn `answers/`.
- `storage/sync.py` – đồng bộ `output/<bài>/` lên R2 (`sync_task`, `safe_sync`): chỉ tải lên, không xóa; manifest hash ở
  `cache/sync_manifest/`; từ chối `output/_fake/`, `input/`, `answers/` và mọi khóa ngoài `R2_OUTPUT_PREFIX`.
- `profiling/` – `quality.py` (profile_problem, data_quality_report), `dictionary.py`, `flags.py`,
  `relationships.py`, `data_summary.py` (bản tóm tắt đã che cho AI).
- `validators/` – `rows.py`, `join.py`, `sums.py`, `ranges.py`, `result.py` (row_count/join/sum/range check).
- `ledger/` – `schema.py`, `ledger.py`: hồ sơ bài ở `output/<bài>/` (results, decisions, findings, open_items,
  data_profile, problem, state, insights, run_log.jsonl; file của lần làm trước ở `work/_superseded/<thời điểm>/`,
  `insights.json` và `report/` bị bỏ ở `_superseded/`; file trong `report/` bị khóa thì `ReportLocked`). Decision có
  status pending, confirmed, superseded; code dùng `active_decisions()`, `decisions()` trả cả superseded để đối chiếu.
- `runner/run_code.py` – chạy code AI: thư mục tạm, input chỉ đọc, bỏ biến bí mật, quét ast, timeout, meta.
- `main.py` – luồng duy nhất: máy trạng thái understand → explore → plan → loop → synthesize → done, lưu `state.json`
  sau mỗi pha, chuyển exception thành mã thoát (`exit_code_for`). `--fake` ghi hồ sơ vào `output/_fake/<bài>/`, không
  đụng hồ sơ thật, không đồng bộ R2.
- `context_builder.py` – dựng prompt vừa ngân sách token theo role (`build_prompt`, `fit_prompt`, `min_input_tokens`,
  `estimate_tokens`): `PROMPT_CUTS` khai báo phần cắt được và thứ tự cắt, bậc cuối thu nhỏ data summary bằng
  `build_data_summary`. Mọi prompt gửi AI phải đi qua đây; đề gốc, câu hỏi hiện tại, contract không bao giờ cắt.
- `analysis/` – `loop.py` (hàm của từng pha: hiểu đề, lập kế hoạch, giải từng câu, chấm meta, ghi sổ; `run_loop` và CLI
  chỉ gọi sang `main.py`), `meta_grader.py`.
- `skills/analysis/` – `understand.md`, `plan.md`, `step.md`, `contract.md` (prompt), `schemas.py` (ProblemRestatement,
  AnalysisPlan, AnalysisStep, AgentMeta), `__init__.py` (load_prompt, render_prompt).
- `report/` – `placeholders.py` (điền số, định dạng, `validate_synthesis`, nhãn), `synthesize.py` (pha tổng hợp),
  `tables.py` (`result_tables`: cách duy nhất lấy file bảng của kết quả; `ordered`, `grouped`: thứ tự dòng dùng chung cho
  bảng và biểu đồ), `charts.py` (biểu đồ, `validate_visuals`, biểu đồ dự phòng `with_auto_charts`, tiêu đề `chart_title`),
  `docx_builder.py` (Word), `slides_outline.py`, `build.py` (dựng cả bộ vào `output/<bài>/report/`).
- `skills/report/` – `synthesize.md` (prompt), `schemas.py` (SynthesisOutput, Insight, nội dung `insights.json`),
  `__init__.py` (load_prompt, render_prompt).
- `slides/` – mới có `__init__.py` (chưa làm); dàn ý slide do `report/slides_outline.py` dựng, không gọi AI.
- `templates/data_dictionary_template.md` – mẫu data dictionary.
- `tests/` – pytest `test_*.py`; script chạy tay (`grade.py`, `grade_bai*.py`, `try_ask_model.py`, `list_models.py`,
  `probe_*.py`, `make_*.py`); `tests/llm_eval/` bộ kiểm định model (cases, scoring, run_eval, summary, thresholds.yaml).
- `Dockerfile` – python:3.12-slim, chạy `main.py`.
- Không lên git: `.env`, `.venv/`, `cache/`, `output/`, `test_data/`, `tests/llm_eval/results/`.

## Lệnh thường dùng (PowerShell, trong `.venv`)
```powershell
python -m pytest -q                                   # toàn bộ test (không gọi API thật)
python -m pytest -q tests/test_docx_builder.py        # một file test
python config.py                                      # chỉ in OK/THIẾU cho từng biến .env
python -m storage.r2 list input/                      # liệt kê; answers/ cần --grader
python -m storage.r2 head input/bai0a/CATEGORY.csv
python -m profiling.quality bai0b [--no-samples] [--set TEN=GIATRI] [--range BANG.COT=MIN:MAX]
python -m profiling.data_summary bai1 [--sensitive BANG.cot] [--max-chars N] [--no-samples] [--refresh [--set TEN=GIATRI] [--range BANG.COT=MIN:MAX]]
python -m runner.run_code bai1 path\to\code.py [--step TEN] [--timeout GIAY]
python main.py bai1 [--sensitive BANG.cot] [--restart] [--yes] [--fake] [--no-sync]   # chạy một bài, tiếp từ pha đang dở
python main.py bai1 --resynthesize [--yes] [--fake] [--no-sync]   # chỉ chạy lại pha synthesize (state synthesize hoặc done)
python main.py bai1 --build-report [--fake] [--no-sync]   # dựng lại biểu đồ, Word, hồ sơ, dàn ý từ insights.json, không gọi AI (state done)
python main.py bai1 --redo R3 [--note "góp ý"] [--yes] [--fake] [--no-sync]   # chạy lại một kết quả valid và các kết quả phụ thuộc, rồi tổng hợp lại
python -m analysis.loop bai1 [--sensitive BANG.cot] [--use-fake] [--max-retries 3] [--yes] [--no-sync]   # cùng luồng main.py
python -m storage.sync bai1 [--dry-run]               # đồng bộ hồ sơ lên R2; --dry-run chỉ in số file, không ghi gì
python -m tests.grade_bai0b                           # chấm profiling bài 0b bằng answers/ (quyền grader)
python -m tests.grade_bai0a                           # chấm kết quả bài 0a (quyền grader, đáp án qua grader_download, không ghi cache)
python -m tests.grade bai1                            # chấm một bài (như trên); đối chiếu theo từ khóa, LỆCH/CẦN NGƯỜI XEM cần người xem lại
python -m tests.grade_bai1                            # giống python -m tests.grade bai1
python tests/llm_eval/run_eval.py --list              # số case theo role
python tests/llm_eval/run_eval.py --model <key> --role analysis [--limit N] [--resume] --yes   # GỌI API THẬT
```
`main.py` và `analysis.loop` trả mã: 0 xong, 1 lỗi gọi AI hoặc lỗi R2, 2 hết hạn mức chuỗi model (chạy lại để tiếp tục),
3 dừng (StopLoop: người dùng từ chối, prompt quá dài sau mọi bậc cắt, `--sensitive` khác state, dictionary trên R2 khác
etag lưu trong profile, ...; bản tóm tắt lộ giá trị che; hoặc file trong `report/` bị khóa).
Chạy lại: tiếp từ pha trong `state.json`; câu đã giải lấy từ `Ledger.results_by_question()` (chỉ kết quả valid).
`--sensitive` chốt vào state ở lần chạy đầu, muốn đổi thì `--restart --sensitive ...`. `--restart` chuyển kết quả valid
và mọi decision thành superseded (giữ trong file, không dùng lại, hỏi lại từ đầu), toàn bộ file `work/` sang
`work/_superseded/<thời điểm>/`, `insights.json` và `report/` sang `_superseded/`, đóng open_item của bước xác nhận; run_log giữ nguyên.
Điểm dừng danh sách câu hỏi nhỏ: y chấp nhận, n dừng, s gõ góp ý (vào `user_corrections`, kèm câu hỏi của kế hoạch cũ gửi AI
lập lại, tối đa `ANALYSIS_PLAN_MAX_RETRIES` lần; decision kế hoạch mới không còn thì superseded).
`--resynthesize`: `insights.json` và `report/` sang `_superseded/`, decision chấp nhận kết luận thành superseded rồi hỏi lại, kết quả và
decision khác giữ nguyên; pha sớm hơn thì từ chối, mã 3; không dùng cùng `--restart`, `--sensitive`.
`--build-report` không dùng cùng `--restart`, `--resynthesize`, `--sensitive`.
Báo cáo dựng sau khi chấp nhận kết luận, ở `output/<bài>/report/`: `bao_cao_<bài>.docx`, `ho_so_<bài>.md`, `slides_outline.json`,
`charts/<insight>.png`; lỗi dựng báo cáo không đổi state done, sửa rồi `--build-report`. `REPORT_AUTHOR` trong `.env` là tùy chọn.
`--redo`: in danh sách rồi hỏi y/n trước khi đổi gì; kết quả thành superseded, phụ thuộc stale (`retire_result`), file của chúng sang
`work/_superseded/`, câu cần chạy lại và góp ý ở `state.redo_notes` (không dùng cache), rồi về pha loop; không dùng cùng `--restart`,
`--resynthesize`, `--build-report`, `--sensitive`. Dừng giữa chừng thì chạy lại không có cờ. Điểm dừng có cờ: y, s (góp ý, chạy lại câu đó,
tối đa `ANALYSIS_PLAN_MAX_RETRIES` lần), n.
`--restart`, `--resynthesize`, `--build-report`, `--redo`: file trong `report/` đang bị khóa (mở trong Word) thì dừng trước mọi thao tác ghi,
mã 3, không đổi gì, kể cả run_log.
Đồng bộ R2 (trừ `--fake`, `--no-sync`): trước mỗi câu hỏi xác nhận, khi xong và khi thoát vì lỗi; lỗi đồng bộ chỉ cảnh
báo một dòng, ghi sự kiện `sync` trong run_log, lần sau tải lại nhờ manifest. `run_task` gọi trực tiếp thì mặc định tắt.
Lọc output tiếng Việt bằng `Select-String` cần `[Console]::OutputEncoding = [Text.Encoding]::UTF8`.

## Quy tắc bất biến
1. Chỉ gọi AI qua `llm.ask_model` (bộ kiểm định dùng `ask_one_model`). Chỉ `llm/client.py` import litellm.
   Không dùng `litellm.Router`, không dùng tool calling: đầu ra AI là JSON kiểm bằng pydantic.
2. AI viết code và quyết định "làm gì"; code (runner + validators) tính mọi con số. Không để AI gõ số.
3. Dữ liệu nguồn chỉ đọc (runner chép ra thư mục tạm, đặt chỉ đọc).
4. Không đọc `answers/` trong code của agent; chỉ `tests/grade.py` và `tests/grade_*.py` được đọc qua quyền grader (`grader=True`).
5. Khóa API và R2 chỉ ở `.env`: không in, không commit, không đưa vào prompt, log hay tài liệu.
6. AI không nhận dữ liệu thô, chỉ bản tóm tắt (`ai_summary` / `build_data_summary`) đã che cột nhạy cảm.
7. Prompt nằm trong file `.md` (vd. `skills/analysis/*.md`), không viết cứng trong code.
8. Ngưỡng và hằng số nằm trong `config.py`.
9. Thay đổi hành vi mà chưa chắc thì hỏi người dùng trước khi sửa.

## Quy ước làm việc
- Mỗi bước xong: chạy `python -m pytest -q`, báo số test pass. Test cũ hỏng thì dừng và báo.
- Không commit khi chưa được yêu cầu. Commit message dạng `Ngay N: ...` (tiếng Việt không dấu).
- Không sửa module của ngày trước nếu không bắt buộc; nếu sửa thì nói rõ sửa gì, vì sao.
- Lỗi lặp lại ở nhiều bài: thêm quy tắc/phép kiểm vào phần cố định (contract, validators, meta_grader)
  thay vì nhắc AI thêm trong prompt. Prompt giữ chung, không gợi ý bẫy của từng bài.
- Test không gọi R2 hay LLM thật: dùng FakeLLM (`use_fake`) và monkeypatch storage.
  Không bật `sync=True` hay gọi `run_cli` không `--fake` trong test khi chưa thay `sync.sync_task` hoặc `r2._write_client`.
- Trả lời người dùng bằng tiếng Việt; tên kỹ thuật giữ nguyên.

## Chỉ đường
- Tài liệu thiết kế (v3.3) và kế hoạch 8 ngày: chưa có trong repo, người dùng giữ bản docx.
- Trạng thái, quyết định đã chốt, việc để sau: `PROGRESS.md`.
- Hợp đồng code AI (đọc/ghi file, file meta, check bắt buộc): `skills/analysis/contract.md`.
