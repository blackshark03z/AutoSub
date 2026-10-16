# AutoSub Cloud V2 — Phase C: Live Pilot Execution Plan

**Document Version:** 1.1.0  
**Date:** 2026-10-16  
**Status:** PROPOSED — STRICTLY BLOCKED UNTIL EXPLICIT OWNER APPROVAL  
**Work Package:** WP-PILOT-001-FIX  
**Project:** AutoSub Cloud V2  
**Branch:** `feat/pilot-e2e-local-sub-overlay`  
**Worker:** Gemini  
**Tech Lead / Auditor:** ChatGPT  
**Priority:** P0  
**Workflow Standard:** Native Workflow Kit v1.3  

---

## 1. Cảnh Báo An Toàn & Ranh Giới Bắt Buộc

> [!CAUTION]
> **KẾ HOẠCH NÀY CHỈ LÀ PHƯƠNG ÁN ĐỀ XUẤT KỸ THUẬT VÀ ĐANG BỊ KHÓA HOÀN TOÀN (BLOCKED).**  
> Tuyệt đối KHÔNG tự ý thực hiện bất kỳ hành động nào dưới đây cho đến khi có **văn bản chấp thuận rõ ràng của Owner**:  
> - **KHÔNG** tải media payload thật từ Bilibili hoặc Kylin về máy.  
> - **KHÔNG** cài đặt/cập nhật phần mềm hay sửa đổi cấu hình máy host của Operator.  
> - **KHÔNG** kích hoạt Docker, WSL hoặc các tác vụ GPU workload nặng.  
> - **KHÔNG** sử dụng tài khoản, cookie, VIP credential cá nhân của Owner.  
> - **KHÔNG** khởi chạy Cloud Download Worker hoặc thuê hạ tầng GPU đám mây.  
> - **KHÔNG** gọi API Gemini trả phí vượt ngoài ngân sách được cấp phép riêng.  
> - **KHÔNG** upload media lên bất kỳ dịch vụ lưu trữ bên thứ ba nào.  
> - **KHÔNG** tìm cách bẻ khóa giao thức app, vượt DRM hay rào cản truy cập.  
> - **KHÔNG** merge nhánh `feat/pilot-e2e-local-sub-overlay` vào `main`.  
> - **KHÔNG** xem các bài test offline dùng mock translation hoặc synthetic/canned OCR bbox là đã kiểm thử model thật (chưa từng kiểm thử model PaddleOCR thật hay LLM dịch thật trên frame donghua live).

---

## 2. Chi Tiết Kế Hoạch Đề Xuất (8 Hạng Mục Bắt Buộc)

### 2.1 Nguồn Được Đề Xuất Thử Nghiệm (Proposed Source)
- **Nguồn được chọn:** **Source A — Bilibili (`https://b23.tv/gK7gvDO`)**, Part 1 (`p=1`, CID `41959818910`).
- **Lý do lựa chọn:**
  - Nguồn A có luồng stream công khai ở độ phân giải 720p/480p không đòi hỏi tài khoản VIP hay cookie cá nhân.
  - Không có tín hiệu mã hóa DRM/CENC.
  - Phù hợp nhất để kiểm chứng chuỗi liên kết: Link → Canonical BV → Part 1 Selection → Bounded Media Excerpt → OCR → Dịch → Mask → Overlay tiếng Việt.
- **Nguồn Source B (Kylin):** **KHÔNG ĐỀ XUẤT** tải live vì bị rào cản `APP_ONLY_RESTRICTED`. Giữ nguyên trạng thái `INGESTION_BLOCKED`.

### 2.2 Độ Dài & Phạm Vi Trích Xuất (Sample Duration & Demux Boundary)
- **Thời lượng mẫu đề xuất:** Giới hạn tối đa **60 giây** (trích xuất có kiểm soát từ đầu Part 1: `00:00:00` đến `00:01:00`).
- **Lưu ý kỹ thuật quan trọng về Byte-Range:**
  - **Không mặc định HTTP byte-range tương đương đoạn 60 giây:** Do video container (MP4/FLV/DASH) sử dụng bitrate biến thiên (VBR) và cấu trúc phân mảnh metadata (`moov`/`mdat`), việc chỉ gửi HTTP Range header theo byte không thể bảo đảm cắt đúng ranh giới thời gian 60 giây và có thể làm hỏng container header.
  - **Cơ chế trích đoạn bắt buộc:** Phải sử dụng công cụ demuxer/stream slicing hiểu container (chẳng hạn `yt-dlp --download-sections "*00:00:00-00:01:00"` hoặc FFmpeg stream seek `-ss 0 -t 60` trực tiếp trên URL stream đã phân giải), tuyệt đối không đoán byte-range thuần túy.
  - Tuyệt đối không tải toàn bộ 7.8 giờ của Part 1 hay 11.9 giờ của cả container.

### 2.3 Các Thao Tác & Engine Sẽ Dùng (Operations & Engines)
1. **Ingestion:** Phân giải URL stream công khai và trích xuất đúng đoạn giới hạn 60s bằng stream slicer có kiểm soát container header.
2. **Video Probe:** Dùng FFprobe cục bộ đo container, xác thực video 720p và audio AAC.
3. **OCR Phụ đề gốc:** Dùng PaddleOCR cục bộ (tiến trình nhẹ) quét các khung hình chứa chữ trong ROI đáy để trích xuất chữ Hán và tọa độ bbox thật. *(Lưu ý: Đây sẽ là lần chạy model OCR thật đầu tiên; các test trước đó chỉ là synthetic detector/mock).*
4. **Dịch Thuật Tiếng Việt:** 
   - *Phương án ưu tiên:* Dùng bộ từ điển/mô hình dịch offline Argos zh→vi nếu có sẵn.
   - *Phương án dự phòng:* Dùng Gemini API với key đã cấu hình an toàn trong `secrets\gemini_api.txt` cho đúng số câu thoại thực tế trích xuất được.
5. **Render `LOCAL_SUB_MASK_OVERLAY`:** 
   - FFmpeg `drawbox` màu đen đặc `@1.0` che kín đúng bbox chữ Trung gốc (padding 2–4px).
   - FFmpeg `subtitles` (libass) đè phụ đề tiếng Việt cỡ tương đương, căn giữa chuẩn xác, wrap tối đa 2 dòng nếu dài.
6. **Output Validation:** FFprobe kiểm tra độ toàn vẹn, đảm bảo drift < 50ms và audio không bị mất.

### 2.4 Môi Trường Dự Kiến (Target Environment)
- **Máy trạm:** Môi trường Windows 11/10 x86_64 hiện tại của Operator.
- **Python:** Python 3.11.9.
- **FFmpeg:** FFmpeg 9.0.1-essentials đã cài sẵn trên hệ thống.
- **Bộ nhớ đĩa khả dụng yêu cầu:** Tối thiểu 2 GB trống tại ổ đĩa phát triển.

### 2.5 Tài Nguyên Dự Kiến (Resource Consumption)
- **Băng thông mạng & Thời gian xử lý:** Không cam kết con số cố định trước khi phương thức stream slice được đo đạc trực tiếp trên live network; ước tính định tính: tải chỉ trong phạm vi đoạn ngắn được phê duyệt, RAM tiêu thụ cục bộ duy trì trong ngưỡng thông thường của workstation (< 2 GB), không gây quá tải CPU/GPU.
- **Chi phí đám mây:** 0 VND (nếu dịch offline) hoặc chi phí không đáng kể (< 0.002 USD nếu dùng Gemini cho các câu thoại trong 60s).

### 2.6 Tác Động Lên Máy Host (Host Impact)
- **Không thay đổi cấu hình hệ thống:** Không sửa registry, không đổi biến môi trường toàn cục, không cài service ngầm.
- **Toàn bộ tệp trung gian được cô lập:** Nằm trọn vẹn trong thư mục tạm `scratch/live_pilot_run/`.
- **Tệp nguồn gốc:** Được lưu dưới dạng read-only và xóa ngay sau khi nghiệm thu.

### 2.7 Tiêu Chí PASS / FAIL (Acceptance Criteria)
Một bài chạy Live Pilot chỉ được xem là **PASS** khi thỏa mãn đồng thời cả 7 tiêu chí:
1. Tải về thành công đoạn trích xuất <= 60s mà không phát sinh lỗi HTTP 403 / 429.
2. FFprobe xác thực tệp đầu ra đọc được, sai lệch drift < 50ms.
3. Không làm biến dạng hay mất luồng âm thanh gốc.
4. Chữ Hán gốc được che kín hoàn toàn bởi hộp đen đặc nhỏ gọn (padding 2–4px), không để lộ viền chữ.
5. Phụ đề tiếng Việt hiển thị rõ ràng, đúng ngữ nghĩa, cỡ chữ tương đương (sai số <= 15%), nằm đúng vị trí chữ cũ.
6. Hộp đen biến mất hoàn toàn trong các khoảng lặng giữa các câu thoại.
7. Toàn bộ tiến trình kết thúc với exit code = 0.

### 2.8 Quy Trình Dừng Khẩn Cấp, Rollback & Dọn Dẹp (Stop, Rollback & Cleanup)
- **Dừng khẩn cấp:** Nếu phát hiện lỗi mạng kéo dài > 30s, dung lượng tệp vượt quá 50 MB, hoặc CPU/RAM tăng bất thường > 80%: Tiến trình lập tức gửi tín hiệu `SIGTERM` hủy FFmpeg và dừng tiến trình.
- **Rollback:** Không ghi đè bất kỳ tệp mã nguồn hoặc cấu hình nào của dự án.
- **Dọn dẹp:** Toàn bộ artifacts và tệp trung gian trong thư mục tạm `scratch/live_pilot_run/` sẽ được dọn dẹp bằng Python an toàn qua `shutil.rmtree()` ngay sau khi kết thúc thử nghiệm; không phụ thuộc vào công cụ ngoài chưa có trong repository.

---

## 3. Khuyến Nghị Gửi Tech Lead & Owner

Gemini đề xuất:
1. **Nghiệm thu hoàn chỉnh Offline Scope (G1 → G5)** sau khi hoàn tất tích hợp tự động và sửa mask tight closure.
2. Giữ nguyên trạng thái **`G6 (Live E2E) = NOT RUN` / `Phase C = BLOCKED`**.
3. Chỉ đề xuất thao tác live thật sau khi toàn bộ chuỗi offline integration đã được Tech Lead và Owner phê duyệt.

---
*Kế hoạch thực thi Live Pilot lập theo tiêu chuẩn Native Workflow Kit v1.3.*
