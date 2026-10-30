# CLAUDE.md

## Dự án
AI agent phân tích dữ liệu: một agent, nhiều skill (hiện có `skills/analysis`). Chạy local; dữ liệu đề bài nằm trên
Cloudflare R2 (bucket đọc qua `storage/r2.py`, có cache ở `cache/`). Chủ dự án mới học lập trình, làm theo kiểu
vibe code với Claude Code trên Windows 11, VSCode, PowerShell; Python 3.12 trong `.venv`.

Trạng thái từng ngày: xem `PROGRESS.md`.

## Cấu trúc thư mục
- `config.py` – đọc `.env`, mọi hằng số/ngưỡng (profiling, validators, summary, runner, vòng lặp, prompt).
- `config/llm.yaml` – model, hạn mức, chuỗi fallback theo vai trò (analysis/report/slide), settings.
- `llm/` – lớp gọi AI: `client.py` (ask_model, ask_one_model; nơi duy nhất import litellm), `cache.py`, `usage.py`,
  `structured.py` (JSON + pydantic), `anonymize.py`, `fake.py` (FakeLLM), `errors.py`.
- `storage/r2.py` – list/đọc/ghi R2, `download_with_cache` (so etag), `read_table` (csv/parquet), chặn `answers/`.
- `profiling/` – `quality.py` (profile_problem, data_quality_report), `dictionary.py`, `flags.py`,
  `relationships.py`, `data_summary.py` (bản tóm tắt đã che cho AI).
- `validators/` – `rows.py`, `join.py`, `sums.py`, `ranges.py`, `result.py` (row_count/join/sum/range check).
- `ledger/` – `schema.py`, `ledger.py`: hồ sơ bài ở `output/<bài>/` (results, decisions, findings, open_items,
  data_profile, problem, run_log.jsonl).
- `runner/run_code.py` – chạy code AI: thư mục tạm, input chỉ đọc, bỏ biến bí mật, quét ast, timeout, meta.
- `analysis/` – `loop.py` (vòng lặp: lập kế hoạch → giải từng câu → chấm meta → ghi sổ), `meta_grader.py`.
- `skills/analysis/` – `plan.md`, `step.md`, `contract.md` (prompt), `schemas.py` (AnalysisPlan, AnalysisStep,
  AgentMeta), `__init__.py` (load_prompt, render_prompt).
- `report/`, `slides/` – mới có `__init__.py` (chưa làm).
- `templates/data_dictionary_template.md` – mẫu data dictionary.
- `tests/` – pytest `test_*.py`; script chạy tay (`grade_bai0b.py`, `try_ask_model.py`, `list_models.py`,
  `probe_*.py`, `make_*.py`); `tests/llm_eval/` bộ kiểm định model (cases, scoring, run_eval, summary, thresholds.yaml).
- `main.py` – còn rỗng. `Dockerfile` – python:3.12-slim (chưa COPY `analysis/`, xem PROGRESS.md).
- Không lên git: `.env`, `.venv/`, `cache/`, `output/`, `test_data/`, `tests/llm_eval/results/`.

## Lệnh thường dùng (PowerShell, trong `.venv`)
```powershell
python -m pytest -q                                   # toàn bộ test (không gọi API thật)
python config.py                                      # chỉ in OK/THIẾU cho từng biến .env
python -m storage.r2 list input/                      # liệt kê; answers/ cần --grader
python -m storage.r2 head input/bai0a/CATEGORY.csv
python -m profiling.quality bai0b [--no-samples] [--set TEN=GIATRI] [--range BANG.COT=MIN:MAX]
python -m profiling.data_summary bai1 [--sensitive BANG.cot] [--max-chars N] [--no-samples] [--refresh]
python -m runner.run_code bai1 path\to\code.py [--step TEN] [--timeout GIAY]
python -m analysis.loop bai1 [--sensitive BANG.cot] [--use-fake] [--max-retries 3] [--yes]
python -m tests.grade_bai0b                           # chấm profiling bài 0b bằng answers/ (quyền grader)
python -m tests.grade_bai0a                           # chấm kết quả bài 0a (quyền grader, đáp án qua grader_download, không ghi cache)
python -m tests.grade_bai1                            # chấm bài 1 (như trên); đối chiếu theo từ khóa, LỆCH/CẦN NGƯỜI XEM cần người xem lại
python tests/llm_eval/run_eval.py --list              # số case theo role
python tests/llm_eval/run_eval.py --model <key> --role analysis [--limit N] [--resume] --yes   # GỌI API THẬT
```
`analysis.loop` trả mã: 0 xong, 1 lỗi gọi AI, 2 hết hạn mức chuỗi model, 3 dừng (StopLoop: người dùng từ chối,
prompt quá dài, ...).
Lọc output tiếng Việt bằng `Select-String` cần `[Console]::OutputEncoding = [Text.Encoding]::UTF8`.

## Quy tắc bất biến
1. Chỉ gọi AI qua `llm.ask_model` (bộ kiểm định dùng `ask_one_model`). Chỉ `llm/client.py` import litellm.
   Không dùng `litellm.Router`, không dùng tool calling: đầu ra AI là JSON kiểm bằng pydantic.
2. AI viết code và quyết định "làm gì"; code (runner + validators) tính mọi con số. Không để AI gõ số.
3. Dữ liệu nguồn chỉ đọc (runner chép ra thư mục tạm, đặt chỉ đọc).
4. Không đọc `answers/` trong code của agent; chỉ `tests/grade_*.py` được đọc qua quyền grader (`grader=True`).
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
- Trả lời người dùng bằng tiếng Việt; tên kỹ thuật giữ nguyên.

## Chỉ đường
- Tài liệu thiết kế (v3.3) và kế hoạch 8 ngày: chưa có trong repo, người dùng giữ bản docx.
- Trạng thái, quyết định đã chốt, việc để sau: `PROGRESS.md`.
- Hợp đồng code AI (đọc/ghi file, file meta, check bắt buộc): `skills/analysis/contract.md`.
