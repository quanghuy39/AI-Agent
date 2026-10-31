# BACKLOG

Cập nhật: 31/10/2026, sau nghiệm thu Ngày 8. Mỗi mục: vấn đề – bằng chứng – hướng sửa.

- **P1:** làm sai kết luận, hoặc chặn việc sửa kết quả. Làm trước.
- **P2:** chất lượng báo cáo, độ tin cậy, an toàn dữ liệu khi mở rộng.
- **P3:** tiện ích, dọn dẹp, tính năng để sau.

## P1

### Phán đoán BI (bài học Ngày 6)
- (a) So sánh nhóm không kiểm định – bài 2: kênh p = 0,196, chiến dịch p = 0,508, `cost_tier` p = 0,120 mà R15 vẫn ghi "Tăng", chắc chắn "Cao" – bắt buộc kiểm định có hiệu chỉnh nhiều phép so sánh trong contract và `meta_grader` trước khi cho ghi mức chắc chắn.
- (b) Không kiểm thứ tự thời gian giữa các bảng – 35,86% lượt tiếp cận (100.444/280.103) trước ngày đăng ký, cả kế hoạch và báo cáo không nêu, tháng 2/2026 chưa trọn cũng không nêu – thêm kiểm thứ tự thời gian và kỳ dữ liệu trọn vẹn vào profiling, thành cờ.
- (c) Decision required bị duyệt gộp – giả định khác hỏi chung bằng một câu `y` – mọi decision required hỏi riêng từng cái, không gộp theo kind.
- (d) `tests.grade` quét cả file dòng chi tiết – 712.742 số vì có `temp_touches.csv`, `temp_orders.csv`, chạy hơn 5 phút, dòng 308 khớp nhầm `ty_le_clicked` với `conversion_rate` – bỏ qua file dòng chi tiết, chỉ so cùng tên cột.

### Chặn nhầm kết quả đúng
- Check join bị chặn nhầm – `--redo R13` lần 2, 3 tính đúng 47.770 đơn duy nhất mà vẫn rớt: `_join_declaration_mismatch` thấy một `row_count` trước sau bằng nhau trên ORDER (không liên quan phép join) nên coi join nổ dòng là khai sai, dừng mã 3 sau 4 lần – chỉ báo lỗi khi `row_count` đó đúng là của phép join, không thì để thành cờ cho người xem; đổi câu thông báo, bỏ gợi ý "bảng trung gian" khi join nổ dòng là có chủ ý.

### Điểm dừng và sửa kết quả
- Join nổ dòng mà vẫn cho đi tiếp – R13 280.103 → 9.900.061 dòng (34 lần), người dùng trả lời `y` (D26), kết quả vẫn `valid` – join nhiều-nhiều không khai `expected_ratio` thì gửi lại AI trước khi hỏi, buộc nêu lý do và cách chống đếm trùng.

### An toàn dữ liệu
- File trung gian chứa `customer_id` thô trên R2 `output/`: bắt buộc sửa trước khi dùng dữ liệu công ty – `temp_touches.csv`, `temp_orders.csv` (41 MB) của R13, và từ lần `--redo` thêm `order_cleaned.csv`, `touch_cleaned.csv`, `attributed_orders.csv`, `campaign_order_attribution.csv`, đều ở `output/bai2/work/`, AI tự ghi để phép kiểm join đọc lại – phép kiểm join nhận cột cần thiết, chặn đồng bộ file `work/` có cột che, dọn file đã có trên R2 kèm sửa manifest.
- File của lần thử rớt nằm lại `work/` – runner chép file về khi code chạy trọn, kể cả lần thử bị chấm rớt; 7 file của Q4 còn ở `work/`, lần sau AI thấy và có thể đọc nhầm, và đã lên R2 – chỉ chép về khi lần thử đạt, hoặc chuyển sang `_superseded/` khi rớt.

### Tổng hợp báo cáo
- Insight rỗng ở yêu cầu đề xuất – I6 "...dựa trên tệp kết quả đề xuất được tạo là có": `result_summary` của R15 chỉ có 2 số đếm và 1 cờ true, đề xuất nằm trong csv mà AI tổng hợp không thấy – bắt buộc `result_summary` chứa nội dung trả lời, đưa dòng đã che của bảng kết quả nhỏ vào prompt synthesize.
- `validate_synthesis` bỏ lọt insight không có nội dung – hàm chỉ kiểm mã nội bộ, chữ số tự gõ, %, placeholder, độ phủ yêu cầu – thêm phép kiểm: yêu cầu có từ "đề xuất" phải có insight chứa giá trị ngoài số đếm và cờ true/false, hoặc đọc được đề xuất từ bảng.
- Kết luận chỉ nhắc lại đề – mục 4 Word bài 2 không có số, không có khuyến nghị, chỉ viết "đã được so sánh", "đã được đánh giá"; `_too_similar` chỉ so với summary – kiểm conclusion phải có câu trả lời cho từng yêu cầu, so thêm với danh sách yêu cầu.
- Hạn chế chung chung – mục 7 không nêu thiếu chi phí thật, nhiễu thống kê hay đếm trùng – yêu cầu hạn chế dẫn từ cờ và decision thật.
- Giá trị boolean được điền vào câu văn – "với khẳng định có", "là có" – chặn placeholder kiểu bool trong văn bản.

### Trung thực của kết quả phân tích
- AI gõ cứng kết luận trong code – `"campaigns_bring_orders": True` (R13), `"recommendation_file_created": True` (R15), không tính từ dữ liệu – quét ast: giá trị trong `result_summary` không được là hằng số.
- Check custom tự khen – R15 khai "Đã sử dụng dữ liệu cost_tier" chỉ vì `len > 0`, quy tắc đề xuất không dùng cost_tier dù `method` nói có; R12 check pass mà reason ghi "found", "is empty" – `meta_grader` không tin `passed` của check custom khi điều kiện không liên quan tới rủi ro đã khai.
- Suy luận nhân quả không có đối chứng – "các chiến dịch thực sự đem lại đơn hàng": chỉ 13,41% đơn có touch trong 7 ngày trước, không có nhóm so sánh – thêm quy tắc chung: khẳng định "đem lại", "do" cần đối chứng, nếu không phải ghi là tương quan.
- Quy gán trùng giữa chiến dịch – 7.236 đơn (15,15%) tính cho từ 2 chiến dịch, tổng theo chiến dịch 57.073 so với 47.770 đơn duy nhất; từng dòng như 7.321 thì đúng – nêu rõ trong báo cáo hoặc dùng quy gán một lần chạm.
- Nhãn "Đã kiểm chứng" dễ hiểu nhầm – I2, I3 ghi đã kiểm chứng cho "cao nhất" trong khi chênh lệch là nhiễu – đổi thành "Số liệu đã kiểm", tách với mức chắc chắn của kết luận.
- Ngưỡng cắt tùy tiện – D28 lấy 5,1%: Loyalty Bonus 0,05101 "Tăng", Seasonal Sale 0,05094 "Giữ" – ngưỡng quyết định phải dựa trên kiểm định, không dựa số gần trung bình.

## P2

### Báo cáo
- Cột bảng sai nghĩa – Bảng 5 cột "Tổng số lượt tiếp cận" là số cặp sau lọc (7.531), số thật 35.180 – kiểm nhãn với tên cột gốc, bắt AI đặt tên cột rõ nghĩa.
- Bảng đề xuất thiếu lý do – Bảng 6 có "Đề xuất", bỏ `reason`, `certainty` dù đề bài yêu cầu – bắt buộc đưa cột trả lời yêu cầu vào bảng.
- Câu vụng – "37 tháng/năm", "khẳng định có", tiêu đề bảng là danh sách tên cột, tỷ lệ 0,0513 thay vì 5,13% – quy tắc văn phong, đơn vị theo tên trường.
- Decision nội bộ lọt vào Word – mục 6.2 có câu "chấp nhận R13 dù có cờ ..." – lọc decision `accept_flags` khỏi mục giả định.
- Insight chỉ sâu bằng số trong `result_summary` – AI thấy tên file, tên cột, không thấy giá trị dòng – chung hướng sửa với I6.
- AI né luật chữ số bằng số viết chữ ("mục sáu") – `validate_synthesis` không chặn – thêm phép kiểm số viết chữ.
- Quy tắc tránh từ đánh giá mức độ chỉ nằm trong prompt – chưa có phép kiểm code – thêm danh sách từ vào `validate_synthesis`.
- `Q<số>` nghĩa quý bị coi là mã nội bộ – AI phải viết bằng lời – phân biệt theo ngữ cảnh.
- Kiểm định report là sàng lọc trên prompt Ngày 2 – chưa chạy trên prompt synthesize thật – chạy lại với prompt hiện tại.
- Prompt synthesize 2.756/3.000 ký tự (`test_prompt_length`) – thêm quy tắc phải rút chỗ khác – tách phần cố định sang phép kiểm code.
- Diễn đạt vụng I3 `bai0a`; bảng nhiều chiều không có `sort` lệch thứ tự với hình (Bảng 2, Hình 2 `bai0a`) – dùng chung `ordered`.

### Phân tích
- Code bước sau đoán sai tên cột file bước trước – Q3 `KeyError: 'total_touches'`, Q5 `KeyError: 'touch_count'`, tốn 2 lượt AI (31.011 token cho hai câu) – đưa danh sách cột của file bước trước vào prompt step.
- Phản hồi cho AI chỉ có lỗi của lần ngay trước – `--redo R13` lần 4 ghi meta sai chỗ (`work/meta_q4.json`), AI không còn thấy lỗi lần trước – nêu rõ khi thấy file meta sai chỗ, giữ lỗi các lần trước trong `error_feedback`.
- Hết số lần thử không để lại gì để dùng – Q4 có kết quả đúng ở lần 2 nhưng cả 4 lần đều bỏ – cho người dùng xem lỗi rồi quyết định, thay vì dừng hẳn.
- File `work/` không có giới hạn cỡ – file trung gian 41 MB của R13 nằm trong `output_path` – đặt giới hạn cỡ file `work/`.
- Giả định cũ còn hiệu lực sau `--redo` – decision do bước cũ thêm (`new_assumptions`) không gắn với kết quả nên vẫn confirmed và gửi cho AI – lưu id kết quả trong decision, superseded cùng kết quả.
- Sau `n` ở điểm dừng có cờ, lần chạy sau hỏi lại qua "Xử lý dữ liệu bất thường", `s` ở đó là sửa chữ của decision, không chạy lại câu – hiện dùng `--redo` – gộp về cùng điểm dừng y/s/n.
- Dictionary bài 1, 2 nhắc bảng EXTERNAL_FACTOR, PRICE_CHANGE không có trong dữ liệu – `data_dictionary.md` – profiling ghi lưu ý hoặc sửa nguồn.
- Kế hoạch dồn nhiều yêu cầu vào 1 câu hỏi – log các lần chạy – thêm phép kiểm độ phủ.
- Decision trùng ý (D6, D7 `bai0a`) – pha giải – so trùng trước khi thêm.
- Model không ổn định giữa các lần chạy – hai lần chạy cùng câu cho cách tính khác nhau – ghi định nghĩa vào decision trước khi giải.
- `plan.md` sát trần – 5.993/6.000 ký tự (`test_prompt_lengths`) – chuyển quy tắc sang phép kiểm code.
- Kiểm định analysis cũ – prompt đã đổi từ sau 30/10; 3/4 model trong chuỗi chưa validated – chạy lại; thử lại gemini_3_8_flash, gemma_4_31b khi Google ổn định; đủ 12 case cho groq_gpt_oss_120b.
- `--restart` trúng cache – lần chạy Ngày 8: understand, plan đều `cache_hit`, ra đúng kế hoạch cũ – bỏ qua cache cho prompt sau restart khi người dùng muốn.
- `findings.json` không superseded khi `--restart` – còn F1, F2 của R3, R8; `tests/grade.py` vẫn đối chiếu – đánh dấu cùng kết quả.
- Cờ mới sau khi kế hoạch đã chấp nhận chưa thành decision – chỉ đăng ký ở pha plan – đăng ký thêm ở pha loop.
- `tests/grade.py` đối chiếu theo từ khóa – bài 2 ĐÃ NÊU 2/12 nhưng #6 chỉ trùng từ, đọc tay chỉ #9 là nêu thật – người chấm vẫn phải đọc; cân nhắc tiêu chí có cấu trúc.
- `state.sensitive` chỉ lưu `--sensitive` – thẻ dictionary đổi giữa chừng không tính vào so sánh, chỉ bị etag chặn – lưu danh sách che đầy đủ.
- Che giá trị chỉ thay chuỗi khớp nguyên văn (>= 4 ký tự); reason của check custom lấy nguyên từ meta – chuẩn hóa trước khi so.
- Báo cáo cũ gắn `source_key` theo ngưỡng mặc định – báo cáo không lưu ngưỡng đã dùng – lưu ngưỡng và `--range` vào profile, lúc đó `--refresh` tự giữ được.

### An toàn và hạ tầng
- `data_profile.json` chứa dòng mẫu thô, kể cả cột che, được đồng bộ lên `output/` – cần bỏ trước khi dùng dữ liệu công ty.
- `enforce_validation` còn false – lệch thiết kế 6.7; bật thì analysis còn 1 model, slide rỗng; cờ áp cho mọi vai trò – đổi thành theo vai trò, đủ model validated rồi bật.
- Runner không phải sandbox thật – quét tĩnh lách được bằng ghép chuỗi lúc chạy – sandbox Docker (xem P3).
- Đồng bộ chưa có bản đồ file hiệu lực – 23 file cũ ở `work/` trên R2 lẫn với file mới; hiện đọc `results.json` – tải bản đồ file đang hiệu lực lên R2.
- Báo cáo cũ còn trên R2 khi `--redo` dở dang – local đã chuyển `report/`, `insights.json` sang `_superseded/` nhưng `output/bai2/report/` trên R2 vẫn là bản dẫn R13 đã superseded – chung hướng bản đồ file, hoặc tải dấu "báo cáo chưa hiệu lực".
- README và CLAUDE.md chưa liệt kê "hết số lần thử" trong các lý do mã 3 – thêm một cụm từ.
- `Start-Transcript` không ghi output của `python` trong PowerShell 5.1 – `bai2_redo_ngay8.txt` rỗng – dùng `run_log.jsonl` để xem lý do.
- `insights.json` cũ còn trên R2 tới khi bị ghi đè – đồng bộ không xóa – chung hướng trên.
- Manifest đồng bộ chỉ ở một máy – máy khác tải lại toàn bộ; object xóa tay không được tải lại tới khi file đổi – so etag R2 thay vì tin manifest.
- Cache input có thể cũ khi R2 lỗi (có cảnh báo `R2_UNREACHABLE`) – dừng hẳn khi cần chính xác.
- Đồng bộ lỗi mạng – lần chạy bài 2 có 1 file `EndpointConnectionError`, chạy tay lại được – thử lại sau cùng trước khi thoát.
- `test_interrupted_at_conclusion_reasks_without_calling_ai`, `test_build_report_does_not_call_ai_and_rebuilds` từng rớt khi máy tải cao – chưa rõ nguyên nhân – tránh chạy cùng việc nặng, xem lại khi lặp.
- `python config.py` chỉ kiểm 4 biến khóa cố định – hãng mới không được kiểm – đọc `api_key_env` từ `llm.yaml`.

## P3

### Tính năng để sau
- Xuất slide `.pptx` – mới có `slides_outline.json`, `slides/` trống – dựng từ dàn ý.
- Canva API – chưa làm – thêm sau khi có `.pptx`.
- PySpark – mới có engine python, `DEFAULT_ENGINE` chưa được code dùng – thêm khi dữ liệu vượt pandas.
- Sandbox Docker cho code AI – hiện runner chạy trên máy – chạy từng bước trong container.
- Nhiều mức thận trọng – chỉ có `chuan`, `CAUTION_LEVEL` khác thì cảnh báo – thêm mức.
- Đọc Excel – chỉ nhận csv, parquet – thêm vào `read_table`.
- Dictionary dạng tự do – chỉ nhận mẫu `templates/` – thêm bước AI chuẩn hóa, người xác nhận.
- Khoảng hợp lệ cho cột ngày – mới hỗ trợ cột số – mở rộng cú pháp.
- Số kiểu Việt Nam (1.234,5) – chưa nhận dạng – thêm vào profiling và đọc dữ liệu.

### Dọn dẹp
- `--fake` trên hồ sơ `_fake` cũ dừng mã 3, `--refresh` không đụng `_fake` – phải xóa tay `data_profile.json` – cho `--fake` tự profile lại.
- Đồng bộ tải cả file tạm `~$*` của Word – thêm vào danh sách bỏ qua.
- Bản sao thừa `_superseded/report_20261031_131229_032292` còn trên R2 và `output/bai0a/` – xóa tay kèm sửa manifest.
- `run_log.jsonl` luôn đổi do sự kiện `sync` – dòng log lượt cuối chỉ lên R2 lần sau; với `--yes` chỉ đồng bộ khi kết thúc – chấp nhận hoặc tách log sync.
- `output_path` thiếu file ở hồ sơ tạo trước Ngày 7 – đã bù bằng `run_log` trong `result_tables` – bỏ khi không còn hồ sơ cũ.
- Hồ sơ `bai1` trước Ngày 6 không có `state.json`, `question_key` – lần đầu giải lại mọi câu – dùng `--restart`.
- Bài `done` chạy lại chỉ báo xong, không đối chiếu `--sensitive` – thêm kiểm.
- `runner.OUTPUT_DIR`, `quality.OUTPUT_DIR` đổi tạm biến toàn cục cho `--fake` – truyền tham số.
- Open_item không tự đóng hết – rà soát từng loại.
- Chuỗi `slide` trong `llm.yaml` chưa kiểm định, slide hiện không gọi AI – bỏ khi chưa dùng.
- Ước lượng token = số ký tự / 3,4 + 700 – so với số thật trong log, chỉnh hệ số.
- Tên riêng bị viết thường trong tiêu đề biểu đồ ("việt Nam") – nhận diện danh từ riêng.
- `run_eval` chỉ lưu `finish_reason` lần gọi cuối – llm/ trả theo từng lần thử.
- Hạn mức thấp: Gemini 3.8 Flash 20 lượt/ngày, OpenRouter 50 lượt/ngày – theo dõi khi chạy nhiều bài.

## Usage lần chạy bài 2 (31/10/2026, sau `--restart`)

Toàn bộ 10 lượt đi qua `gemini_3_5_flash_lite` (model đầu chuỗi, validated), không chuyển model, `fallback_count` = 0, không có `model_switched`.

| Hãng | Lượt | Gọi mạng | Trúng cache | Token vào | Token ra | Tổng token | Thời gian (giây) |
|---|---|---|---|---|---|---|---|
| gemini | 10 | 8 | 2 (understand, plan) | 43.865 | 14.053 | 57.918 | 43,6 |
| mistral, groq, openrouter | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Token cache không tính vào bảng: 5.807 vào, 871 ra.

| Bước | Lượt | Token vào | Token ra | Tổng |
|---|---|---|---|---|
| Q5 (2 lần) | 2 | 12.351 | 3.727 | 16.078 |
| Q3 (2 lần) | 2 | 11.866 | 3.067 | 14.933 |
| Q4 | 1 | 5.511 | 1.932 | 7.443 |
| Q2 | 1 | 5.280 | 1.620 | 6.900 |
| Q1 | 1 | 5.093 | 1.600 | 6.693 |
| synthesize | 1 | 3.764 | 2.107 | 5.871 |
