import streamlit as st
import sqlite3
import json
import time
import PyPDF2
from groq import Groq
import re
import html  # for unescaping HTML entities
import pandas as pd  # For dashboard graph

# ---------------------------------------------------------------------------------
# ------------------------- CONFIGURATION -----------------------------------------
# ---------------------------------------------------------------------------------
API_MODEL = "gemma2-9b-it"
GROQ_API_KEY = "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"

# ---------------------------------------------------------------------------------
# ------------------------- DATABASE SETUP ----------------------------------------
# ---------------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute(
        '''CREATE TABLE IF NOT EXISTS users (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT UNIQUE,
                 password TEXT,
                 analytics TEXT
         )'''
    )
    # Check and add analytics column if missing
    c.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in c.fetchall()]
    if "analytics" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN analytics TEXT")
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------------------------------
# --------------------- FLASHCARD SESSION DATABASE SETUP --------------------------
# ---------------------------------------------------------------------------------
def init_session_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute(
        '''CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                session_name TEXT,
                flashcards TEXT,
                created_at TEXT
         )'''
    )
    conn.commit()
    conn.close()

init_session_db()

def update_user_analytics(username, analytics):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("UPDATE users SET analytics=? WHERE username=?", (json.dumps(analytics), username))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------------
# --------------------- FLASHCARD SESSION FUNCTIONS -------------------------------
# ---------------------------------------------------------------------------------
def create_session(session_name, flashcards):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO sessions (username, session_name, flashcards, created_at) VALUES (?, ?, ?, ?)",
              (st.session_state["user"], session_name, json.dumps(flashcards), created_at))
    conn.commit()
    conn.close()

def get_sessions(username):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id, session_name, flashcards, created_at FROM sessions WHERE username=?", (username,))
    sessions = c.fetchall()
    conn.close()
    return sessions

def delete_session(session_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE id=? AND username=?", (session_id, st.session_state["user"]))
    conn.commit()
    conn.close()

# NEW: Function to retrieve community sessions (sessions from other users)
def get_community_sessions(current_user):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id, username, session_name, flashcards, created_at FROM sessions WHERE username != ?", (current_user,))
    sessions = c.fetchall()
    conn.close()
    return sessions

# ---------------------------------------------------------------------------------
# ------------------------- AUTHENTICATION FUNCTIONS ------------------------------
# ---------------------------------------------------------------------------------
def login(username, password):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
    user = c.fetchone()
    conn.close()
    
    default_analytics = {
        "pdfs_uploaded": 0,
        "flashcards_generated": 0,
        "flashcards_viewed": 0,
        "tests_taken": 0,
        "last_test_score": 0,
        "test_insights": "",
        "test_history": []  # To store date, time, and score of each test
    }
    
    if user:
        st.session_state["user"] = username
        try:
            st.session_state["analytics"] = json.loads(user[3]) if user[3] else default_analytics
        except Exception:
            st.session_state["analytics"] = default_analytics
        st.rerun()
    else:
        st.error("Invalid login credentials")

def signup(username, password):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    try:
        initial_analytics = json.dumps({
            "pdfs_uploaded": 0,
            "flashcards_generated": 0,
            "flashcards_viewed": 0,
            "tests_taken": 0,
            "last_test_score": 0,
            "test_insights": "",
            "test_history": []
        })
        c.execute("INSERT INTO users (username, password, analytics) VALUES (?, ?, ?)",
                  (username, password, initial_analytics))
        conn.commit()
        st.success("Signup successful. Please log in.")
    except sqlite3.IntegrityError:
        st.error("Username already exists")
    conn.close()

# ---------------------------------------------------------------------------------
# ------------------------- PDF TEXT EXTRACTION -----------------------------------
# ---------------------------------------------------------------------------------
def extract_text_from_pdf(pdf_file):
    text = ""
    reader = PyPDF2.PdfReader(pdf_file)
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted
    return text

# ---------------------------------------------------------------------------------
# --------------------- HELPER FUNCTION FOR FLASHCARDS ----------------------------
# ---------------------------------------------------------------------------------
def clean_flashcard_text(text: str) -> str:
    """
    1. Unescape HTML entities.
    2. Remove HTML tags.
    3. Remove common markers (Front:, Back:, etc.).
    4. Strip whitespace.
    """
    text = html.unescape(text)
    text = re.sub(r"<[^>]*>", "", text)
    markers = ["** front **", "**Front**", "(back front)", "** back **", "Front:", "Back:"]
    for marker in markers:
        text = text.replace(marker, "")
    return text.strip()

# ---------------------------------------------------------------------------------
# ------------------------- GROQ AI FUNCTIONS -------------------------------------
# ---------------------------------------------------------------------------------
def generate_flashcard_question_groq(pdf_text, user_input, prev_question=""):
    client = Groq(api_key=GROQ_API_KEY)
    max_text_length = 1500
    pdf_text = pdf_text[:max_text_length]

    prompt_template = (
        "You are an expert tutor helping a student understand complex documents.\n"
        "The document content is as follows:\n\n"
        "'{pdf_text}'\n\n"
        "The student is interested in the following topic/question: '{user_input}'.\n\n"
        "Please generate ONE clear and concise flashcard question that tests a key concept from the document.\n"
        "- Do not include the answer in your response.\n"
        "- If a previous flashcard question was provided ('{prev_question}'), ensure the new question is different.\n"
        "- Output only plain text, starting with 'Question:' and then the question. No HTML.\n"
    )
    prompt = prompt_template.format(pdf_text=pdf_text, user_input=user_input, prev_question=prev_question)
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=API_MODEL
        )
        question_text = chat_completion.choices[0].message.content.strip()
        return clean_flashcard_text(question_text)
    except Exception as e:
        st.error(f"Error generating flashcard question: {str(e)}")
        return "Error generating flashcard question."

def generate_flashcard_answer_groq(pdf_text, question):
    client = Groq(api_key=GROQ_API_KEY)
    max_text_length = 1500
    pdf_text = pdf_text[:max_text_length]

    prompt_template = (
        "You are an expert tutor helping a student understand complex documents.\n"
        "The document content is as follows:\n\n"
        "'{pdf_text}'\n\n"
        "Given the flashcard question below:\n\n"
        "'{question}'\n\n"
        "Please provide a detailed and accurate answer in plain text only.\n"
        "Do not include any labels like 'Answer:' or use any HTML. Just return the text.\n"
    )
    prompt = prompt_template.format(pdf_text=pdf_text, question=question)
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=API_MODEL
        )
        answer_text = chat_completion.choices[0].message.content.strip()
        return clean_flashcard_text(answer_text)
    except Exception as e:
        st.error(f"Error generating flashcard answer: {str(e)}")
        return "Error generating flashcard answer."

def generate_simplified_explanation_groq(pdf_text, answer):
    client = Groq(api_key=GROQ_API_KEY)
    max_text_length = 1500
    pdf_text = pdf_text[:max_text_length]

    prompt_template = (
        "You are an expert tutor. The student did not understand the following answer:\n\n"
        "'{answer}'\n\n"
        "Based on the original document:\n\n"
        "'{pdf_text}'\n\n"
        "Please re-explain this answer in simpler terms, step by step, avoiding complex jargon.\n"
        "Return only plain text, with no HTML.\n"
    )
    prompt = prompt_template.format(pdf_text=pdf_text, answer=answer)
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=API_MODEL
        )
        simplified_text = chat_completion.choices[0].message.content.strip()
        return clean_flashcard_text(simplified_text)
    except Exception as e:
        st.error(f"Error simplifying explanation: {str(e)}")
        return "Error simplifying explanation."

def generate_test_questions_groq(pdf_text):
    client = Groq(api_key=GROQ_API_KEY)
    max_text_length = 1500
    pdf_text = pdf_text[:max_text_length]

    prompt_template = (
        "Generate 10 multiple-choice questions from this document:\n\n"
        "'{pdf_text}'\n\n"
        "For each question, provide a JSON object with keys:\n"
        "  'question': plain text of the question,\n"
        "  'options': a list of 4 possible answers in plain text,\n"
        "  'correct': the correct option in plain text.\n"
        "Return as a JSON array, with no extra text or HTML.\n"
    )
    prompt = prompt_template.format(pdf_text=pdf_text)
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=API_MODEL
        )
        response_text = chat_completion.choices[0].message.content.strip()
        start_index = response_text.find('[')
        end_index = response_text.rfind(']')
        if start_index != -1 and end_index != -1:
            response_text = response_text[start_index:end_index+1]
        test_questions = json.loads(response_text)
        return test_questions
    except Exception as e:
        st.error(f"Error generating test questions: {str(e)}")
        return []

def generate_test_insights_groq(score, wrong_qas=None):
    if wrong_qas is None:
        wrong_qas = []
    client = Groq(api_key=GROQ_API_KEY)
    prompt = (
        f"You are an expert tutor. Provide detailed learning insights for a test score of {score}/10.\n"
        "Return only plain text, with no HTML.\n"
    )
    if wrong_qas:
        wrong_details = "\n".join([f"Question: {item['question']}\nAnswer: {item['answer']}" for item in wrong_qas])
        prompt += (
            "The student answered the following questions incorrectly:\n" +
            wrong_details +
            "\nPlease analyze the mistakes and suggest areas for improvement."
        )
    else:
        prompt += "No specific wrong questions provided."
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=API_MODEL
        )
        return clean_flashcard_text(chat_completion.choices[0].message.content.strip())
    except Exception as e:
        st.error(f"Error generating test insights: {str(e)}")
        return ""

# ---------------------------------------------------------------------------------
# ------------------------- MAIN USER INTERFACE -----------------------------------
# ---------------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="EduFlash", layout="wide", page_icon="📚")
    
    st.markdown("""
    <style>
    [data-testid="stMetricValue"] {
        font-size: 1.5rem !important;
        color: #4CAF50 !important;
    }
    .flashcard {
        background: #1E1E1E;
        border-radius: 10px;
        padding: 2rem;
        margin: 1rem 0;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    .stButton>button {
        background: #4CAF50 !important;
        color: white !important;
        border: none !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Sidebar menu now includes "Saved Flashcards" and the new "Community" feature
    if "user" not in st.session_state:
        menu = ["Login", "Sign Up"]
    else:
        menu = ["Dashboard", "Flashcards", "Test", "Saved Flashcards", "Community", "Logout"]
    
    choice = st.sidebar.selectbox("Menu", menu, key="menu")
    
    # ---------------------- LOGIN SECTION ----------------------------------------
    if choice == "Login":
        with st.container():
            st.title("🔒 Login")
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            if st.button("Login", use_container_width=True):
                login(username, password)
                
    # ---------------------- SIGN UP SECTION --------------------------------------
    elif choice == "Sign Up":
        with st.container():
            st.title("📝 Sign Up")
            username = st.text_input("Choose Username", key="signup_username")
            password = st.text_input("Choose Password", type="password", key="signup_password")
            if st.button("Create Account", use_container_width=True):
                signup(username, password)
                
    # ---------------------- LOGOUT SECTION ---------------------------------------
    elif choice == "Logout":
        st.session_state.clear()
        st.rerun()
    
    # ---------------------- DASHBOARD SECTION ------------------------------------
    elif choice == "Dashboard":
        st.title("📊 Learning Dashboard")
        st.write(f"Welcome back, {st.session_state['user']}! 🎉")
        analytics = st.session_state.get("analytics", {})
        
        cols = st.columns(4)
        metrics = [
            ("PDFs Uploaded", "📄", analytics.get("pdfs_uploaded", 0)),
            ("Flashcards Generated", "🔄", analytics.get("flashcards_generated", 0)),
            ("Tests Taken", "📝", analytics.get("tests_taken", 0)),
            ("Avg Score", "🎯", f"{analytics.get('last_test_score', 0)}/10")
        ]
        for col, (title, icon, value) in zip(cols, metrics):
            col.metric(title, f"{icon} {value}")
        
        with st.expander("Detailed Analytics"):
            st.write(f"**Flashcards Viewed:** {analytics.get('flashcards_viewed', 0)}")
            st.write(f"**Last Test Insights:** {analytics.get('test_insights', 'N/A')}")
        
        # Test History Graph and Table
        with st.expander("Test History"):
            test_history = analytics.get("test_history", [])
            if test_history:
                df = pd.DataFrame(test_history)
                df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"])
                df = df.sort_values("datetime")
                chart_data = df.set_index("datetime")["score"]
                st.line_chart(chart_data)
                st.table(df[["date", "time", "score"]])
            else:
                st.write("No test history available yet.")
    
    # ---------------------- FLASHCARDS SECTION -----------------------------------
    elif choice == "Flashcards":
        st.title("📖 Flashcard Generator")
        
        # Initialize current session flashcards if not already set
        if "session_flashcards" not in st.session_state:
            st.session_state["session_flashcards"] = []
        
        # Upload PDF and extract text
        uploaded_file = st.file_uploader("Upload PDF", type="pdf", key="pdf_uploader")
        
        if uploaded_file or "pdf_text" in st.session_state:
            if "pdf_text" not in st.session_state:
                pdf_text = extract_text_from_pdf(uploaded_file)
                st.session_state["pdf_text"] = pdf_text
                if "pdf_uploaded_once" not in st.session_state:
                    st.session_state["analytics"]["pdfs_uploaded"] += 1
                    update_user_analytics(st.session_state["user"], st.session_state["analytics"])
                    st.session_state["pdf_uploaded_once"] = True
            else:
                pdf_text = st.session_state["pdf_text"]
            
            # Get user query for flashcards
            user_input = st.text_input("Enter topic/question for flashcards:", key="flashcard_query")
            if user_input and "current_flashcard_question" not in st.session_state:
                question = generate_flashcard_question_groq(pdf_text, user_input, prev_question="")
                st.session_state["current_flashcard_question"] = question
                st.session_state["current_flashcard_answer"] = ""
                st.session_state["flashcard_reveal"] = False
                st.session_state["analytics"]["flashcards_generated"] = st.session_state["analytics"].get("flashcards_generated", 0) + 1
                update_user_analytics(st.session_state["user"], st.session_state["analytics"])
            elif st.button("Generate Flashcard"):
                question = generate_flashcard_question_groq(pdf_text, user_input, prev_question=st.session_state.get("current_flashcard_question", ""))
                st.session_state["current_flashcard_question"] = question
                st.session_state["current_flashcard_answer"] = ""
                st.session_state["flashcard_reveal"] = False
                st.session_state["analytics"]["flashcards_generated"] = st.session_state["analytics"].get("flashcards_generated", 0) + 1
                update_user_analytics(st.session_state["user"], st.session_state["analytics"])
            
            # Display the flashcard question
            if "current_flashcard_question" in st.session_state:
                safe_question = clean_flashcard_text(st.session_state["current_flashcard_question"])
                if safe_question.lower().startswith("question:"):
                    safe_question = safe_question[9:].strip()

                with st.container():
                    st.markdown(f"""
                    <div class="flashcard">
                        <p style='text-align:center;margin-top: 1rem;'>
                            <strong>Question:</strong> {safe_question}
                        </p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Reveal Answer and subsequent actions
                if not st.session_state["flashcard_reveal"]:
                    if st.button("Reveal Answer"):
                        answer = generate_flashcard_answer_groq(pdf_text, safe_question)
                        st.session_state["current_flashcard_answer"] = answer
                        st.session_state["flashcard_reveal"] = True
                        st.rerun()
                else:
                    safe_answer = clean_flashcard_text(st.session_state["current_flashcard_answer"])
                    with st.container():
                        st.markdown(f"""
                        <div class="flashcard">
                            <p style='text-align:center;margin-top: 1rem;'><strong>Answer:</strong> {safe_answer}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    if st.button("Didn't Understand"):
                        simpler_answer = generate_simplified_explanation_groq(pdf_text, safe_answer)
                        st.session_state["current_flashcard_answer"] = simpler_answer
                        st.rerun()
                    
                    # Button to add the current flashcard to the session
                    if st.button("Add Flashcard to Session"):
                        flashcard_data = {
                            "question": safe_question,
                            "answer": safe_answer,
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        st.session_state["session_flashcards"].append(flashcard_data)
                        st.success("Flashcard added to session.")
                        st.rerun()
                
                # Button to generate the next flashcard (avoiding repetition)
                if st.button("Next Flashcard"):
                    prev_question = st.session_state["current_flashcard_question"]
                    new_question = generate_flashcard_question_groq(pdf_text, user_input, prev_question=prev_question)
                    st.session_state["current_flashcard_question"] = new_question
                    st.session_state["current_flashcard_answer"] = ""
                    st.session_state["flashcard_reveal"] = False
                    st.session_state["analytics"]["flashcards_viewed"] += 1
                    update_user_analytics(st.session_state["user"], st.session_state["analytics"])
                    st.rerun()
            
            # Display current session flashcards (not yet saved permanently)
            if st.session_state["session_flashcards"]:
                st.markdown("### Current Flashcard Session")
                st.write(f"Total flashcards in session: {len(st.session_state['session_flashcards'])}")
                for idx, fc in enumerate(st.session_state["session_flashcards"], start=1):
                    st.write(f"**Flashcard {idx}:**")
                    st.write(f"Q: {fc.get('question', '')}")
                    st.write(f"A: {fc.get('answer', '')}")
                    st.write(f"Added on: {fc.get('timestamp', '')}")
                    st.write("---")
                
                # Button to save the entire current session permanently
                if st.button("Save Flashcard Session"):
                    session_name = "Session " + time.strftime("%Y-%m-%d %H:%M:%S")
                    create_session(session_name, st.session_state["session_flashcards"])
                    st.success("Flashcard session saved permanently.")
                    st.session_state["session_flashcards"] = []
                    st.rerun()
        else:
            st.info("Please upload a PDF to generate flashcards.")
    
    # ---------------------- TEST SECTION -----------------------------------------
    elif choice == "Test":
        st.title("📝 Knowledge Test")
        if "pdf_text" not in st.session_state:
            st.info("No PDF uploaded yet. Please upload a PDF in the Flashcards section to generate a test.")
        else:
            if "test_questions" not in st.session_state:
                if st.button("Generate Test Questions"):
                    test_questions = generate_test_questions_groq(st.session_state["pdf_text"])
                    if test_questions:
                        st.session_state["test_questions"] = test_questions
                    else:
                        st.error("Failed to generate test questions. Please try again.")
            
            if "test_questions" in st.session_state:
                answers = {}
                for i, q in enumerate(st.session_state["test_questions"]):
                    with st.container():
                        st.subheader(f"Question {i+1}")
                        question_text = clean_flashcard_text(q["question"])
                        options = [clean_flashcard_text(opt) for opt in q["options"]]
                        answers[i] = st.radio(question_text, options, key=f"q_{i}")
                
                if st.button("Submit Test"):
                    score = 0
                    wrong_qas = []
                    for i, q in enumerate(st.session_state["test_questions"]):
                        correct_answer = clean_flashcard_text(q["correct"])
                        if answers.get(i) == correct_answer:
                            score += 1
                        else:
                            wrong_qas.append({
                                "question": question_text,
                                "answer": answers.get(i)
                            })
                    
                    insights = generate_test_insights_groq(score, wrong_qas)
                    current_date = time.strftime("%Y-%m-%d")
                    current_time = time.strftime("%H:%M:%S")
                    
                    st.session_state["analytics"].update({
                        "tests_taken": st.session_state["analytics"].get("tests_taken", 0) + 1,
                        "last_test_score": score,
                        "test_insights": insights
                    })
                    st.session_state["analytics"].setdefault("test_history", []).append({
                        "date": current_date,
                        "time": current_time,
                        "score": score
                    })
                    update_user_analytics(st.session_state["user"], st.session_state["analytics"])
                    
                    st.success(f"Score: {score}/10")
                    with st.expander("Test Insights"):
                        st.write(insights)
                    
                    st.subheader("Test Review")
                    for i, q in enumerate(st.session_state["test_questions"]):
                        st.write(f"**Question {i+1}:**")
                        st.write(f"**Your Answer:** {answers.get(i, 'No answer')}")
                        st.write(f"**Correct Answer:** {clean_flashcard_text(q['correct'])}")
                        if answers.get(i) == clean_flashcard_text(q['correct']):
                            st.success("✅ Correct")
                        else:
                            st.error("❌ Incorrect")
                        st.write("---")
                    
                    del st.session_state["test_questions"]
    
    # ---------------------- SAVED FLASHCARDS SECTION -------------------------------
    elif choice == "Saved Flashcards":
        st.title("💾 Saved Flashcard Sessions")
        sessions = get_sessions(st.session_state["user"])
        if sessions:
            for session in sessions:
                session_id, session_name, flashcards_json, created_at = session
                st.markdown(f"**{session_name}** (Created: {created_at})")
                try:
                    flashcards = json.loads(flashcards_json) if flashcards_json else []
                except Exception:
                    flashcards = []
                if flashcards:
                    for idx, fc in enumerate(flashcards, start=1):
                        st.write(f"**Flashcard {idx}:**")
                        st.write(f"Q: {fc.get('question', '')}")
                        st.write(f"A: {fc.get('answer', '')}")
                        st.write(f"Saved on: {fc.get('timestamp', '')}")
                        st.write("---")
                if st.button("Delete Session", key=f"delete_session_{session_id}"):
                    delete_session(session_id)
                    st.success("Session deleted.")
                    st.rerun()
        else:
            st.info("No saved flashcard sessions available.")
    
    # ---------------------- COMMUNITY SECTION --------------------------------------
    elif choice == "Community":
        st.title("🌐 Community Flashcards")
        # Retrieve sessions from other users
        sessions = get_community_sessions(st.session_state["user"])
        if sessions:
            for session in sessions:
                session_id, username, session_name, flashcards_json, created_at = session
                st.markdown(f"**{session_name}** by **{username}** (Created: {created_at})")
                try:
                    flashcards = json.loads(flashcards_json) if flashcards_json else []
                except Exception:
                    flashcards = []
                if flashcards:
                    for idx, fc in enumerate(flashcards, start=1):
                        st.write(f"**Flashcard {idx}:**")
                        st.write(f"Q: {fc.get('question', '')}")
                        st.write(f"A: {fc.get('answer', '')}")
                        st.write(f"Saved on: {fc.get('timestamp', '')}")
                        st.write("---")
        else:
            st.info("No community flashcard sessions available.")

if __name__ == "__main__":
    main()

