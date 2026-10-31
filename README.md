# AI Agent phân tích dữ liệu

Agent nhận một đề bài và bộ dữ liệu trên Cloudflare R2, tự hiểu đề, lập kế hoạch, viết code phân tích, kiểm tra kết quả
rồi dựng báo cáo Word, hồ sơ bài toán và dàn ý slide. AI viết code và lời văn; mọi con số do code tính và kiểm chứng.
Người dùng xác nhận ở các điểm dừng.

Trạng thái hiện tại: [PROGRESS.md](PROGRESS.md). Việc còn lại: [BACKLOG.md](BACKLOG.md). Quy tắc khi sửa code: [CLAUDE.md](CLAUDE.md).

## Cài đặt

Cần Python 3.12, chạy trên Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env       # rồi điền giá trị
python config.py                  # chỉ in OK/THIẾU, không in giá trị
python -m pytest -q               # không gọi R2 hay AI thật
```

## Cấu trúc thư mục

| Thư mục, file | Vai trò |
|---|---|
| `main.py` | Điểm vào, chạy các pha understand → explore → plan → loop → synthesize → done |
| `config.py`, `config/llm.yaml` | Hằng số, ngưỡng; model và chuỗi dự phòng theo vai trò |
| `llm/` | Lớp gọi AI: chuỗi dự phòng, cache, ghi usage, kiểm JSON |
| `storage/` | Đọc, ghi R2 và đồng bộ hồ sơ |
| `profiling/` | Kiểm tra chất lượng dữ liệu, đọc dictionary, tạo bản tóm tắt đã che cho AI |
| `analysis/`, `skills/analysis/` | Vòng phân tích và prompt |
| `runner/` | Chạy code AI viết trong thư mục tạm, input chỉ đọc, bỏ biến bí mật |
| `validators/` | Kiểm số dòng, join, tổng, khoảng giá trị |
| `ledger/` | Sổ hồ sơ của bài ở `output/<bài>/` |
| `report/`, `skills/report/` | Tổng hợp, biểu đồ, Word, hồ sơ, dàn ý slide |
| `templates/` | Mẫu data dictionary |
| `tests/` | Test tự động, script chấm, bộ kiểm định model (`tests/llm_eval/`) |

`.env`, `.venv/`, `cache/`, `output/`, `test_data/` không lên git.

## Biến môi trường (`.env`)

| Biến | Bắt buộc | Ghi chú |
|---|---|---|
| `R2_ENDPOINT`, `R2_BUCKET`, `R2_INPUT_PREFIX`, `R2_OUTPUT_PREFIX` | Có | Prefix có dấu `/` ở cuối |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Có | Token chỉ đọc |
| `R2_WRITE_ACCESS_KEY_ID`, `R2_WRITE_SECRET_ACCESS_KEY` | Có | Token đọc và ghi, dùng để đồng bộ `output/` |
| `LLM_CONFIG_PATH` | Có | Nên để đường dẫn tương đối `config/llm.yaml`, dùng được cả trong Docker |
| `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` | Theo chuỗi | Cần khóa của mọi model nằm trong chuỗi đang dùng; thiếu thì lỗi nêu tên biến |
| `CAUTION_LEVEL` | Không | Chỉ hỗ trợ `chuan` |
| `DEFAULT_ENGINE` | Không | `python config.py` có kiểm tra, code chưa dùng |
| `REPORT_AUTHOR` | Không | Tên người lập, in trên trang bìa |

Khóa chỉ nằm trong `.env`. Không đưa khóa vào code, prompt, log hay tài liệu.

## Cấu hình model (`config/llm.yaml`)

- `settings`: cấu hình chung.
  - `enforce_validation`: `true` thì chỉ chạy model có `validated: true`. Hiện là `false`, xem mục Giới hạn.
  - `cooldown_s`, `cooldown_max_s`: thời gian tạm loại một model sau khi hết hạn mức hoặc lỗi lặp lại.
  - `cooldown_not_found_s`: thời gian tạm loại khi model trả 404.
  - `cooldown_whole_provider`: hãng mà 429 tạm loại mọi model của hãng đó.
  - `num_retries`, `allowed_fails`, `json_retries`, `timeout_s`: số lần thử lại và thời gian chờ.
- `models`: mỗi model có `provider`, `litellm_model`, `api_key_env` (tên biến, không phải khóa), `limits`,
  `max_input_tokens`, `max_tokens`, `data_note`.
- `roles`: mỗi vai trò (`analysis`, `report`, `slide`) có `chain` là danh sách `{model, validated}` theo thứ tự thử.

Cách xử lý lỗi: 429, 404, 5xx và timeout chuyển model kế tiếp (5xx và timeout thử lại trước). 400, 401, 403 dừng ngay,
không chuyển model. Hết cả chuỗi thì dừng mã 2, chạy lại sau để tiếp tục. Prompt được cắt theo `max_input_tokens`
nhỏ nhất của chuỗi, nên một model giới hạn thấp sẽ kéo trần của cả chuỗi xuống.

### Thêm, bớt model

1. Thêm mục vào `models` (tên biến khóa nên kết thúc bằng `_API_KEY`, vì runner chỉ bỏ các biến theo mẫu này khỏi môi
   trường của code AI) và đặt khóa trong `.env`.
2. Thêm vào `chain` của vai trò, tạm để `validated: false`.
3. Chạy bộ kiểm định. Lệnh này gọi API thật, tốn hạn mức:

```powershell
python tests/llm_eval/run_eval.py --list
python tests/llm_eval/run_eval.py --model <key> --role analysis --yes
python tests/llm_eval/summary.py     # so với tests/llm_eval/thresholds.yaml
```

4. Đạt ngưỡng thì đặt `validated: true`. Sửa prompt trong `skills/` thì nên chạy lại kiểm định.

Bớt model: xóa khỏi `chain`, không cần sửa code.

## Chuẩn bị bài mới trên R2

```text
input/<bài>/de_bai.md            đề bài (đúng tên này), nên liệt kê từng yêu cầu thành dòng riêng
input/<bài>/data_dictionary.md   theo templates/data_dictionary_template.md
input/<bài>/<BANG>.csv           dữ liệu, csv hoặc parquet, tên file trùng tên bảng trong dictionary
answers/<bài>/                   đáp án, chỉ script chấm được đọc, agent không bao giờ đọc
```

Dictionary gồm một tiêu đề `##` cho mỗi bảng, ngay dưới là bảng `Cột | Kiểu | Định nghĩa`. Trong cột Kiểu ghi:

- `(PK)` cho khóa chính, `(FK)` cho khóa ngoại.
- `(SENSITIVE)` cho cột nhạy cảm, kết hợp được: `string (PK, SENSITIVE)`. Giá trị cột này không gửi cho AI, và cột nối
  với nó qua quan hệ cũng được che theo.
- Cột thứ 4 tùy chọn "Khoảng hợp lệ" cho cột số: `1..5`, `>= 0`, `<= 100`.

Cột email, số điện thoại được tự nhận và che. Có thể thêm cột che bằng `--sensitive BANG.cot` ở lần chạy đầu.

Code của agent chỉ ghi vào `output/`, nên `input/` tải lên bằng trang quản lý R2 hoặc công cụ S3.

**Sửa dictionary sau khi đã profile:** lần chạy sau sẽ dừng mã 3 vì etag đã đổi. Chạy lại profile, kèm `--set`, `--range` nếu đã dùng:

```powershell
python -m profiling.data_summary <bài> --refresh [--set TEN=GIATRI] [--range BANG.COT=MIN:MAX]
```

## Chạy

```powershell
python main.py <bài>                      # chạy, hoặc tiếp từ pha đang dở
python main.py <bài> --sensitive BANG.cot # thêm cột che, chốt ở lần chạy đầu
python main.py <bài> --restart            # làm lại từ đầu, kết quả và decision cũ thành superseded
python main.py <bài> --resynthesize       # chỉ chạy lại pha tổng hợp (pha synthesize hoặc done)
python main.py <bài> --build-report       # dựng lại Word, biểu đồ từ insights.json, không gọi AI (pha done)
python main.py <bài> --redo R13 [--note "góp ý"]  # chạy lại một kết quả và các kết quả phụ thuộc, rồi tổng hợp lại
python main.py <bài> --no-sync            # không đồng bộ lên R2
python main.py <bài> --fake               # chạy thử bằng FakeLLM
python -m storage.sync <bài> [--dry-run]  # đồng bộ tay, ví dụ sau khi lỗi mạng
```

### Chạy lại một bước sai (`--redo`)

```powershell
python main.py bai2 --redo R13 --note "Đếm mỗi đơn một lần, nêu rõ đơn gán cho nhiều chiến dịch"
```

Lệnh này in danh sách kết quả sẽ chạy lại (kết quả đó và mọi kết quả valid phụ thuộc nó theo `depends_on`) rồi hỏi y/n. Trả lời `n` thì
hồ sơ không đổi. Trả lời `y` thì:

1. Bản tổng hợp và `report/` cũ sang `_superseded/`, decision chấp nhận kết luận thành superseded, như `--resynthesize`.
2. Kết quả đó thành `superseded`, kết quả phụ thuộc thành `stale`, file của chúng sang `work/_superseded/<thời điểm>/`, decision "chấp nhận
   R.. dù có cờ" của chúng thành superseded.
3. Agent chỉ giải lại các câu đó, không dùng cache, kết quả valid khác giữ nguyên. `--note` chỉ gửi cho câu của kết quả được chỉ định.
   Giả định mới vẫn qua điểm dừng như bình thường.
4. Xong thì tổng hợp, hỏi chấp nhận kết luận và dựng lại báo cáo.

Chỉ dùng được với kết quả `valid` từ pha loop trở đi; id sai hoặc kết quả đã `superseded`, `stale` thì dừng mã 3. Không dùng cùng
`--restart`, `--resynthesize`, `--build-report`, `--sensitive`. File báo cáo đang mở hoặc dictionary đã đổi thì dừng trước khi đổi gì.
Dừng giữa chừng (hết hạn mức, `n`, Ctrl+C) thì chạy `python main.py <bài>` để tiếp tục, góp ý vẫn được giữ, không chạy lại `--redo`.

`--fake` không gọi AI, không đồng bộ, ghi vào `output/_fake/<bài>/` và không đụng hồ sơ thật. Nếu `--fake` dừng mã 3 do
profile cũ thì xóa `output/_fake/<bài>/data_profile.json` rồi chạy lại. `--yes` tự trả lời y, chỉ dùng khi thử.

| Mã thoát | Ý nghĩa | Việc cần làm |
|---|---|---|
| 0 | Xong | Đọc báo cáo |
| 1 | Lỗi gọi AI (cấu hình, khóa, yêu cầu) hoặc lỗi R2 | Đọc thông báo, sửa rồi chạy lại |
| 2 | Hết hạn mức cả chuỗi model, tiến độ đã lưu | Chạy lại sau giờ được in ra |
| 3 | Dừng có chủ đích: người dùng chọn n; một câu hết số lần thử; AI báo không trả lời được; kế hoạch không dùng được; tổng hợp sai sau mọi lần thử; `--sensitive` khác state; dictionary đã đổi; prompt quá dài; bản tóm tắt lộ giá trị che; file trong `report/` đang mở; `--redo`, `--resynthesize` dùng sai id hoặc sai pha | Đọc thông báo, xem lý do từng lần thử ở sự kiện `step_attempt` trong `output/<bài>/run_log.jsonl` (trường `failure`), sửa rồi chạy lại `python main.py <bài>`; file Word đang mở thì đóng lại |

Hết số lần thử của một câu thì mọi lần thử rớt không để lại file: chỉ lần thử đạt mới được chép về `output/<bài>/work/`.

## Trả lời điểm dừng

| Câu hỏi | Trả lời |
|---|---|
| Diễn giải đề bài | `y` đúng, `n` dừng |
| Định nghĩa chỉ số, xử lý dữ liệu bất thường | `y` đồng ý, `s` gõ nội dung khác, `n` dừng |
| Giả định khác | `y` chấp nhận tất cả, số thứ tự để sửa mục đó, `n` dừng |
| Danh sách câu hỏi nhỏ của kế hoạch | `y` chấp nhận, `s` gõ góp ý để AI lập lại, `n` dừng |
| "Vẫn tiếp tục?" khi kết quả có cờ | `y` chấp nhận kết quả có cờ, `s` gõ góp ý để chạy lại câu này (tối đa 3 lần), `n` dừng |
| Chấp nhận kết luận | `y` dựng báo cáo, `n` dừng |

**Khi kết quả có cờ, đọc cờ trước, đừng trả lời `y` theo thói quen:** `y` ghi nhận bạn chấp nhận kết quả đó và các bước sau sẽ dùng nó.
Cờ cho thấy cách làm sai thì chọn `s` và gõ cách sửa: kết quả vừa ghi thành superseded, kết quả phụ thuộc thành stale, agent giải lại câu
đó kèm góp ý. Hết 3 lượt góp ý thì chỉ còn `y`, `n`. Cần thời gian xem kỹ thì chọn `n` để dừng: kết quả vẫn nằm trong sổ, lần chạy sau hỏi
lại ở mục xử lý dữ liệu bất thường, tại đó `s` chỉ sửa chữ của decision; muốn giải lại câu đó thì dùng `--redo`.

## Đọc hồ sơ

`output/<bài>/` có 8 file sổ, cộng `insights.json` sau khi tổng hợp:

| File | Nội dung |
|---|---|
| `problem.json` | Đề bài và diễn giải |
| `data_profile.json` | Báo cáo chất lượng dữ liệu, có dòng mẫu thô |
| `decisions.json` | Định nghĩa, giả định, cách xử lý đã xác nhận |
| `results.json` | Kết quả từng câu: số, phép kiểm, file code, file bảng, phụ thuộc, trạng thái |
| `findings.json` | Phát hiện có cờ |
| `open_items.json` | Việc còn mở |
| `state.json` | Pha hiện tại, kế hoạch, cột che đã chốt |
| `run_log.jsonl` | Nhật ký: mỗi lượt gọi AI có hãng, model, token, số lần chuyển model |

Thư mục `work/` chứa file bảng, `code/` chứa code AI đã chạy. Báo cáo nằm ở `report/`:

- `bao_cao_<bài>.docx`: báo cáo 7 mục. Nhấn cập nhật khi Word hỏi để hiện mục lục.
- `ho_so_<bài>.md`: nên đọc đầu tiên khi cần truy vết, gồm câu hỏi, cách làm, kiểm chứng và quyết định.
- `slides_outline.json`: dàn ý slide, `charts/`: biểu đồ.

Nhãn trong Word: "Đã kiểm chứng" nghĩa là các phép kiểm số liệu đều đạt, **không** có nghĩa là kết luận kinh doanh đúng.
"Phỏng đoán" nghĩa là kết quả hoặc kết quả nó phụ thuộc có cờ.

**File trên R2:** đồng bộ chỉ tải lên, không bao giờ xóa. Sau `--restart`, file cũ vẫn nằm ở `output/<bài>/work/` trên R2, lẫn với
file mới. Muốn biết file nào còn hiệu lực, đọc `results.json`: chỉ `output_path` của kết quả có `status` là `valid` mới dùng được.
Đừng xóa tay trên R2: manifest trong `cache/sync_manifest/` vẫn tưởng file đã có nên sẽ không tải lại.

## Chạy bằng Docker

```powershell
docker build -t ai-agent .
docker run --rm --env-file .env ai-agent -m storage.r2 list input/<bài>/
docker run --rm -it --env-file .env -v "${PWD}/output:/app/output" -v "${PWD}/cache:/app/cache" ai-agent main.py <bài>
```

`ENTRYPOINT` là `python`. `.env` không nằm trong image, chỉ truyền lúc chạy. Không gắn `output/` và `cache/` thì hồ sơ mất khi
container kết thúc. Đã kiểm tra build và đọc R2 trong container; chưa chạy trọn một bài bằng lệnh cuối.

## Giới hạn hiện tại

- Dùng API miễn phí nên có giới hạn hạn mức. Chỉ dùng dữ liệu cá nhân hoặc công khai, không dùng dữ liệu công ty.
- Gói miễn phí của Gemini có thể dùng dữ liệu gửi đi để cải thiện sản phẩm. Bản tóm tắt gửi AI đã che cột nhạy cảm, nhưng tên
  cột, thống kê và mẫu giá trị của cột không che vẫn được gửi.
- `data_profile.json` và file `work/` có thể chứa dòng thô, kể cả cột che, và được đồng bộ lên `output/` của bucket.
- Số liệu đúng, nhưng phán đoán BI chưa đạt: ở bài 2 agent không kiểm định thống kê, xếp hạng theo chênh lệch nhiễu và đề xuất
  thiếu cơ sở. Xem [BACKLOG.md](BACKLOG.md).
- Báo cáo cần người đọc lại trước khi gửi đi.
- `enforce_validation` là `false`: model chưa kiểm định vẫn có thể chạy khi model đứng trước lỗi.
- Runner quét tĩnh và tách môi trường, không phải sandbox thật. Chỉ chạy trên máy của bạn.
