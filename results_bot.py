import requests
import json
import re
import sys
import os
import logging
import datetime
import asyncio  # <-- NEW: For handling async operations in a sync server
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from flask import Flask, request, Response  # <-- NEW: The web server
import gunicorn  # <-- NEW: To run the server (Render needs this)

# --- Try to import FPDF ---
# You MUST install this: pip install fpdf2
try:
    from fpdf import FPDF
except ImportError:
    print("=" * 50)
    print("FATAL ERROR: The 'fpdf2' library is not installed.")
    print("Please install it to use this graphical PDF script:")
    print(">>> pip install fpdf2")
    print("=" * 50)
    sys.exit(1)

# --- Configuration ---
# !! IMPORTANT !!
# Get your token from Render's Environment Variables
# We do this so your token is not exposed in the code.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("--- BOT TOKEN NOT FOUND ---")
    logger.critical("Please set the BOT_TOKEN environment variable on Render.")
    sys.exit(1)

API_URL = "https://btebresultszone.com/results"
EXAM_TYPE = "DIPLOMA IN ENGINEERING"
DEFAULT_REGULATION = 0

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- Core API Logic & PDF Generation (Unchanged) ---
# (Your existing functions get_bteb_headers, get_payload,
# parse_custom_response, fetch_and_parse_result,
# format_result_for_telegram, PDF, _draw_key_value_table,
# and generate_result_pdf go here. They are perfect as-is.)

def get_bteb_headers():
    return {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "text/x-component",
        "Origin": "https://btebresultszone.com",
        "Referer": "https://btebresultszone.com/results",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "next-action": "400c11e29979614b56e818124a2a6b8246a9d8702c",
        "next-router-state-tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22results%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
    }


def get_payload(roll_number):
    return json.dumps([{"roll": int(roll_number), "exam": EXAM_TYPE, "regulation": DEFAULT_REGULATION}])


def parse_custom_response(response_text):
    try:
        start_marker = '1:'
        start_index = response_text.find(start_marker)
        if start_index == -1:
            raise ValueError("Could not find the '1:' marker.")

        raw_json_string = response_text[start_index + len(start_marker):].strip()
        cleaned_json = re.sub(r'"\$undefined"', 'null', raw_json_string)
        cleaned_json = re.sub(r'"\$D([0-9T:\.-Z]+)"', r'"\1"', cleaned_json)
        cleaned_json = re.sub(r'"\$[^"]+"', 'null', cleaned_json)
        return json.loads(cleaned_json)
    except Exception as e:
        logger.error(f"Failed to parse response: {e}")
        return None


def fetch_and_parse_result(roll_number):
    logger.info(f"Fetching result for roll: {roll_number}")
    try:
        headers = get_bteb_headers()
        payload = get_payload(roll_number)
        response = requests.post(API_URL, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        parsed_data = parse_custom_response(response.text)

        if parsed_data and parsed_data.get('success'):
            return parsed_data.get('data'), None
        elif parsed_data:
            return None, "<b>[ ℹ️ INFO ]</b>\nResult not found or request was not successful."
        else:
            return None, "<b>[ 🚫 ERROR ]</b>\nFailed to parse the result data."
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP Error: {e}")
        return None, f"<b>[ 🚫 ERROR ]</b>\nHTTP Error: {e}\nSecurity tokens may have expired."
    except requests.exceptions.RequestException as e:
        logger.error(f"Request Error: {e}")
        return None, f"<b>[ 🚫 ERROR ]</b>\nCould not connect to the server: {e}"


def format_result_for_telegram(result_data):
    roll = result_data.get('roll', 'N/A')
    exam = result_data.get('exam', 'N/A')
    regulation = result_data.get('regulation', 'N/A')
    institute = result_data.get('institute', {}).get('name', 'N/A')
    district = result_data.get('institute', {}).get('district', 'N/A')

    gpa_style = "<i>N/A</i>"
    try:
        gpa_val = result_data['semester_results'][0]['exam_results'][0]['gpa']
        gpa_style = f"<b>{gpa_val}</b> 🔥"
    except (IndexError, KeyError, TypeError):
        pass

    reffereds = result_data.get('current_reffereds') or []
    failed_subjects = [ref for ref in reffereds if not ref.get('passed') and ref.get('subject_code')]

    message = "<b>🇧🇩 BTEB Result Card</b>\n"
    message += "--------------------------------------\n\n"
    message += f"<b>ROLL NUMBER :</b> <code>{roll}</code>\n"
    message += f"<b>LATEST GPA    :</b> {gpa_style}\n\n"
    message += f"<b>Institute :</b> <code>{institute}</code>\n"
    message += f"<b>District  :</b> <code>{district}</code>\n"
    message += f"<b>Exam      :</b> <code>{exam} (Reg: {regulation})</code>\n"
    message += "\n--------------------------------------\n"

    if not failed_subjects:
        message += "<b>🎉 CONGRATULATIONS! 🎉</b>\n"
        message += "You have passed all current subjects."
    else:
        message += f"<b>⚠️ STATUS: {len(failed_subjects)} Referred Subject(s)</b>\n"
        for subject in failed_subjects:
            name = subject.get('subject_name', 'Unknown')
            code = subject.get('subject_code', 'N/A')
            sem = subject.get('subject_semester', 'N/A')
            message += f"  - <code>{code}</code>: {name} (Sem: {sem})\n"
    return message


class PDF(FPDF):
    """Custom PDF class to handle footer"""

    def footer(self):
        # Page footer
        self.set_y(-30)
        self.set_font('Helvetica', '', 10)
        self.cell(w=0, h=10, txt="results_bot", border=0, ln=True)
        self.set_font('Helvetica', 'B', 10)
        self.cell(w=0, h=5, txt="Authorized Result Generator", border=0, ln=False)

        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(w=0, h=10, txt=f"Page {self.page_no()}", border=0, ln=False, align='C')


def _draw_key_value_table(pdf, data, x_pos, y_pos, key_width=50, val_width=130):
    """Helper function to draw a clean key-value table."""
    pdf.set_xy(x_pos, y_pos)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_line_width(0.2)
    pdf.set_draw_color(0, 0, 0)

    for key, value in data.items():
        pdf.set_x(x_pos)
        # Draw Key Cell
        pdf.cell(w=key_width, h=8, txt=key, border=1, ln=False, align='L')
        # Draw Value Cell
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(w=val_width, h=8, txt=value, border=1, ln=True, align='L')
        pdf.set_font('Helvetica', '', 11)

    return pdf.get_y()  # Return new Y position


def generate_result_pdf(result_data, roll_number):
    """
    Generates a new 'Professional Report' style PDF, inspired by the user's image.
    This design is limited to the data the API *actually provides*.
    """

    # --- Extract Data ---
    roll = result_data.get('roll', 'N/A')
    exam = result_data.get('exam', 'N/A')
    regulation = result_data.get('regulation', 'N/A')
    institute = result_data.get('institute', {}).get('name', 'N/A')
    institute_code = result_data.get('institute', {}).get('code', 'N/A')

    latest_gpa = "N/A"
    try:
        latest_gpa = result_data['semester_results'][0]['exam_results'][0]['gpa']
    except (IndexError, KeyError, TypeError):
        pass

    reffereds = result_data.get('current_reffereds') or []
    failed_subjects = [ref for ref in reffereds if not ref.get('passed') and ref.get('subject_code')]
    is_pass = not failed_subjects

    # --- Setup PDF Document ---
    pdf = PDF(orientation='P', unit='mm', format='A4')
    pdf.add_page()
    pdf.set_auto_page_break(False)

    try:
        pdf.set_font('Helvetica', '', 12)
    except Exception as e:
        logger.warning(f"Font issue: {e}. Using Arial.")
        pdf.set_font('Arial', '', 12)

    # --- 1. Draw Header (Inspired by Image) ---
    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(w=0, h=10, txt=institute.upper(), border=0, ln=True, align='C')
    pdf.set_font('Helvetica', '', 12)
    pdf.cell(w=0, h=8, txt="Government of the People's Republic of Bangladesh", border=0, ln=True, align='C')

    pdf.set_y(pdf.get_y() + 5)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(w=0, h=10, txt="Student Result Report (Unofficial)", border=0, ln=True, align='C')
    pdf.set_y(pdf.get_y() + 5)

    # --- 2. Draw Student Info Table ---
    student_data = {
        "Roll Number": str(roll),
        "Exam": f"{exam} (Regulation {regulation})",
        "Institute": f"{institute} ({institute_code})",
    }
    y_pos = _draw_key_value_table(pdf, student_data, x_pos=15, y_pos=pdf.get_y(), key_width=40, val_width=150)

    y_pos += 10  # Add spacing

    # --- 3. Draw Result Summary Table ---
    result_status = "PASSED" if is_pass else "REFERRED"

    # --- Custom Drawing for this table to add colors ---
    pdf.set_xy(15, y_pos)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_line_width(0.2)

    # GPA Row
    pdf.cell(w=40, h=8, txt="Total GPA", border=1, ln=False, align='L')
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(w=150, h=8, txt=str(latest_gpa), border=1, ln=True, align='L')

    # Status Row (with color)
    pdf.set_font('Helvetica', '', 11)
    pdf.cell(w=40, h=8, txt="Result Status", border=1, ln=False, align='L')

    status_color = (0, 100, 0) if is_pass else (139, 0, 0)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*status_color)
    pdf.cell(w=150, h=8, txt=result_status, border=1, ln=True, align='L')
    pdf.set_text_color(0, 0, 0)  # Reset color

    # Date Row
    pdf.set_font('Helvetica', '', 11)
    pdf.cell(w=40, h=8, txt="Result Date", border=1, ln=False, align='L')
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(w=150, h=8, txt=datetime.date.today().strftime('%B %d, %Y'), border=1, ln=True, align='L')

    # Verified By Row
    pdf.set_font('Helvetica', '', 11)
    pdf.cell(w=40, h=8, txt="Verified by", border=1, ln=False, align='L')
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(w=150, h=8, txt="results_bot (Unofficial)", border=1, ln=True, align='L')

    y_pos = pdf.get_y() + 10

    # --- 4. Draw Referred Subjects Table (if any) ---
    if failed_subjects:
        pdf.set_xy(15, y_pos)
        pdf.set_font('Helvetica', 'B', 14)
        pdf.cell(w=0, h=10, txt="Referred Subject Details", border=0, ln=True, align='L')
        y_pos += 10

        # Draw Table Header
        pdf.set_xy(15, y_pos)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_fill_color(220, 220, 220)  # Light gray header
        pdf.cell(w=30, h=8, txt="Code", border=1, ln=False, align='C', fill=True)
        pdf.cell(w=130, h=8, txt="Subject Name", border=1, ln=False, align='C', fill=True)
        pdf.cell(w=30, h=8, txt="Semester", border=1, ln=True, align='C', fill=True)

        # Draw Table Rows
        pdf.set_font('Helvetica', '', 10)
        for subject in failed_subjects:
            pdf.set_x(15)
            pdf.cell(w=30, h=8, txt=str(subject.get('subject_code', 'N/A')), border=1, ln=False, align='C')
            pdf.cell(w=130, h=8, txt=str(subject.get('subject_name', 'Unknown')), border=1, ln=False, align='L')
            pdf.cell(w=30, h=8, txt=str(subject.get('subject_semester', 'N/A')), border=1, ln=True, align='C')

    # --- Save PDF ---
    filepath = f"BTEB_Result_{roll_number}.pdf"
    # In a server environment, save to a temporary folder
    # For Render's ephemeral filesystem, this is fine
    pdf.output(filepath)
    logger.info(f"Generated PDF: {filepath}")
    return filepath


# --- Telegram Bot Handlers (Unchanged) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}!\n\n"
        "Welcome to the BTEB Result Bot. I am now running on a live server!\n\n"
        "<b>Usage:</b> <code>/check &lt;roll_number&gt;</code>"
    )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        roll_number = context.args[0]
        if not roll_number.isdigit():
            await update.message.reply_html("<b>[ 🚫 ERROR ]</b>\nInvalid roll number.")
            return
    except (IndexError, ValueError):
        await update.message.reply_html(
            "<b>[ 🚫 ERROR ]</b>\nYou forgot to provide the roll number.\n"
            "<b>Usage:</b> <code>/check &lt;roll_number&gt;</code>"
        )
        return

    processing_msg = await update.message.reply_html(
        f"Checking result for roll <code>{roll_number}</code>..."
    )
    result_data, error_message = fetch_and_parse_result(roll_number)

    if error_message:
        await processing_msg.edit_text(error_message, parse_mode=ParseMode.HTML)
        return
    if not result_data:
        await processing_msg.edit_text("<b>[ 🚫 ERROR ]</b>\nAn unknown error occurred.", parse_mode=ParseMode.HTML)
        return

    # 1. Show Text Result
    html_message = format_result_for_telegram(result_data)
    await processing_msg.edit_text(html_message, parse_mode=ParseMode.HTML)

    # 2. Generate and Send PDF
    pdf_msg = await update.message.reply_text("Generating new 'Professional Report' PDF... 📄")
    try:
        pdf_filepath = generate_result_pdf(result_data, roll_number)

        await update.message.reply_document(
            document=open(pdf_filepath, 'rb'),
            filename=f"BTEB_Result_{roll_number}.pdf",
            caption=f"Here is the new professional report for roll <b>{roll_number}</b>."
        )

        if os.path.exists(pdf_filepath):
            os.remove(pdf_filepath)
        await pdf_msg.delete()

    except Exception as e:
        logger.error(f"Failed to generate or send PDF: {e}")
        await pdf_msg.edit_text(f"<b>[ 🚫 ERROR ]</b>\nFailed to generate the PDF file: {e}", parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "<b>How to use this bot:</b>\n\n"
        "<b>/start</b> - Show the welcome message.\n"
        "<b>/check &lt;roll_number&gt;</b> - Show result and generate a graphical PDF.\n"
        "<b>/help</b> - Show this help message."
    )


# --- !!! NEW: Webhook Server Main Function !!! ---

# Build the Telegram application (without an Updater)
# We will feed it updates manually from our Flask server
application = Application.builder().token(BOT_TOKEN).build()

# Add your command handlers
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("check", check_command))
application.add_handler(CommandHandler("help", help_command))

# Create the Flask web server
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
async def webhook():
    """This is the endpoint Telegram will send updates to."""
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)

        # Process the update asynchronously
        # This runs the full logic (check_command, etc.)
        await application.process_update(update)

        return "OK", 200
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return "Error", 500


@app.route("/")
def index():
    """A simple page to make sure the web server is alive."""
    return "Hello! I am the BTEB Result Bot."

# Note: We do not run app.run() here.
# Gunicorn will be responsible for running the 'app' object.