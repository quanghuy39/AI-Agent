# Mẫu data dictionary

Đặt file tên `data_dictionary.md` cạnh các file dữ liệu của bài. Agent đọc file này để biết khóa,
kiểu và ý nghĩa từng cột, rồi đối chiếu với dữ liệu thật.

## Bốn quy tắc

1. **Mỗi bảng một tiêu đề `##`**, ngay dưới là một bảng markdown 3 cột `Cột | Kiểu | Định nghĩa`
   (tiêu đề tiếng Anh `Column | Type | Definition` cũng được).
2. **Khóa ghi trong cột Kiểu:** khóa chính thêm `(PK)`, khóa ngoại thêm `(FK)`. Khóa chính gồm nhiều cột
   thì ghi `(PK)` ở từng cột.
3. **Tên bảng giống tên file** (không tính đuôi): file `ORDER.csv` → `## ORDER`. Không phân biệt hoa/thường,
   dấu cách, dấu gạch và chữ "s" ở cuối (`## Orders` vẫn khớp `ORDER.csv`).
4. **Cột nhạy cảm ghi `(SENSITIVE)`** trong cột Kiểu (kết hợp được với khóa: `(PK, SENSITIVE)`).
   Giá trị của cột này không bao giờ được gửi cho AI: bản tóm tắt chỉ giữ tên, kiểu, số null, cấu trúc,
   và thay giá trị trong bản ghi mẫu bằng mã ẩn danh.

Kiểu nên dùng: `int`, `float`, `decimal`, `date`, `datetime`, `string`. Cột ngày nên ghi định dạng trong
định nghĩa (ví dụ `dd-mm-yyyy`). Cột không có trong file mà phải tự tính thì ghi rõ "derived" hoặc
"calculated" trong định nghĩa.

## Ví dụ

## ORDER

| Cột | Kiểu | Định nghĩa |
|---|---|---|
| order_id | string (PK) | Mã đơn hàng |
| customer_id | string (FK) | Mã khách hàng, tham chiếu CUSTOMER |
| phone | string (SENSITIVE) | Số điện thoại người nhận |
| order_date | date | Ngày đặt hàng, định dạng dd-mm-yyyy |
| amount | decimal | Giá trị đơn hàng (VND) |
| net_amount | decimal | Derived: amount trừ giảm giá, không có trong file |

## Dạng CSV

Có thể dùng `data_dictionary.csv` thay cho file markdown, mỗi dòng là một cột, gồm 4 cột:
`table, column, type, definition` (hoặc `bang, cot, kieu, dinh nghia`). Ví dụ một dòng:
`ORDER,order_id,string (PK),Mã đơn hàng`.
