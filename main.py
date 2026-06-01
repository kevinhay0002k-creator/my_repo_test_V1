import logging
import os
import uuid
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Import cấu hình và các hàm module nội bộ
import config
from browser import connect_gemini
from requester import send_question, new_chat
from responder import get_respond

# Bật logging để theo dõi tiến trình
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Biến toàn cục quản lý duy nhất 1 driver xuyên suốt vòng đời của bot
driver = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Chào bạn!* Hệ thống điều khiển Gemini qua Chrome Debug Port đã sẵn sàng.\n\n"
        "📌 *Hướng dẫn sử dụng:*\n"
        "1. *Chat bình thường:* Gửi câu hỏi trực tiếp (tiếp tục mạch văn của đoạn chat cũ).\n"
        "2. *Tạo chat mới:* Gõ `/newchat <Câu hỏi>` để xóa sạch vết cũ và bắt đầu phiên mới."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def process_and_respond(update: Update, user_message: str, force_new_chat: bool = False):
    """Hàm lõi xử lý quy trình gửi câu hỏi và nhận phản hồi dưới dạng Markdown"""
    global driver
    
    if driver is None:
        await update.message.reply_text("❌ *Lỗi:* Hệ thống mất kết nối với trình duyệt Chrome. Vui lòng kiểm tra lại phía server.", parse_mode=ParseMode.MARKDOWN)
        return
        
    chat_id_log = str(uuid.uuid4())[:8]  # Định danh duy nhất cho phiên để lưu vết file log
    
    # BƯỚC 2: Lưu nội dung tin nhắn thành file request.md
    with open("request.md", "w", encoding="utf-8") as req_file:
        req_file.write(user_message)
    
    if force_new_chat:
        await update.message.reply_text("🧹 _Đang gửi lệnh tạo phiên chat mới trên Gemini..._", parse_mode=ParseMode.MARKDOWN)
        new_chat(driver)
        time.sleep(1.5)  # Chờ 1.5 giây để giao diện web Gemini kịp chuyển đổi sang trang trắng hoàn toàn
        await update.message.reply_text("⏳ _Đang nạp câu hỏi vào phiên chat mới..._", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("⏳ _Đang nạp câu hỏi vào Gemini web, vui lòng đợi..._", parse_mode=ParseMode.MARKDOWN)

    # BƯỚC 3: Đẩy request lên web qua hàm send_question trong requester.py
    success = send_question(driver, user_message, chatid=chat_id_log, ai_provider=config.AI_PROVIDER)
    if not success:
        await update.message.reply_text("❌ *Lỗi:* Đã xảy ra sự cố khi cố gắng điền thông tin lên giao diện web.", parse_mode=ParseMode.MARKDOWN)
        return

    # BƯỚC 4: Nhận kết quả bằng hàm get_respond trong responder.py
    final_content = get_respond(driver, chatid=chat_id_log, ai_provider=config.AI_PROVIDER)
    if not final_content:
        await update.message.reply_text("❌ *Lỗi:* Không thể trích xuất được câu trả lời từ giao diện Gemini.", parse_mode=ParseMode.MARKDOWN)
        return

    # BƯỚC 5: Lưu kết quả vào file respond.md
    with open("respond.md", "w", encoding="utf-8") as res_file:
        res_file.write(final_content)
        
    # BƯỚC 6: Đọc file respond.md
    if os.path.exists("respond.md"):
        with open("respond.md", "r", encoding="utf-8") as res_file:
            bot_response = res_file.read()
            
        if not bot_response.strip():
            bot_response = "⚠️ _File 'respond.md' trống rỗng!_"
    else:
        bot_response = "❌ *Lỗi:* Không tìm thấy file kết quả đầu ra 'respond.md'."

    # BƯỚC 7: Trả lời bằng nội dung trong file respond.md với định dạng Markdown
    # Cắt nhỏ chuỗi nếu vượt quá giới hạn 4096 ký tự của Telegram API
    try:
        if len(bot_response) > 4095:
            for i in range(0, len(bot_response), 4095):
                await update.message.reply_text(bot_response[i:i+4095], parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(bot_response, parse_mode=ParseMode.MARKDOWN)
    except Exception as telegram_error:
        # Trường hợp Markdown từ Gemini trả về chứa các ký tự đặc biệt lỗi cú pháp bẻ gãy bộ parser của Telegram, 
        # bot sẽ tự động fallback gửi dưới dạng text thuần để không bị crash.
        logging.warning(f"Markdown parse failed, falling back to plain text: {telegram_error}")
        if len(bot_response) > 4095:
            for i in range(0, len(bot_response), 4095):
                await update.message.reply_text(bot_response[i:i+4095])
        else:
            await update.message.reply_text(bot_response)

async def handle_new_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /newchat <Câu hỏi>"""
    if not context.args:
        await update.message.reply_text("⚠️ *Vui lòng nhập câu hỏi sau lệnh.* Ví dụ: `/newchat Xin chào` ", parse_mode=ParseMode.MARKDOWN)
        return
        
    user_message = " ".join(context.args)
    await process_and_respond(update, user_message, force_new_chat=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản thông thường (giữ nguyên ngữ cảnh cũ)"""
    user_message = update.message.text
    await process_and_respond(update, user_message, force_new_chat=False)

def main():
    global driver
    
    # Kết nối tới Chrome đang mở sẵn tab Gemini
    driver = connect_gemini(port=config.CHROME_DEBUG_PORT)
    if driver is None:
        print("❌ Không thể khởi động Bot vì kết nối Chrome thất bại.")
        return
        
    # Khởi tạo Application Telegram Bot
    application = Application.builder().token(config.TELEGRAM_TOKEN).build()

    # Đăng ký handler lệnh và tin nhắn text
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("newchat", handle_new_chat_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Telegram Bot API kết nối Chrome Debug đang chạy hoàn hảo với chế độ hiển thị Markdown...")
    
    # Chạy Polling nhận tin nhắn
    application.run_polling()

if __name__ == '__main__':
    main()
