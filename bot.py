import os
import json
import google.generativeai as palmlib # Gemini API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# 1. 환경 변수에서 열쇠 꺼내기
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
BLOG_ID = os.environ.get('BLOG_ID')
CLIENT_JSON_STR = os.environ.get('CLIENT_SECRET_JSON')

# 2. 제미니 설정 (뇌 깨우기)
palmlib.configure(api_key=GEMINI_KEY)
model = palmlib.GenerativeModel('gemini-3.1-flash-lite')

def run_agent():
    # 3. 제미니에게 오늘 뭐 할지 물어보기 (자율성 부여)
    prompt = """
    너는 이제부터 이 블로그의 주인이야. 
    오늘의 주제를 네 마음대로 정해서 블로그 포스팅을 하나 작성해줘.
    형식은 JSON으로 줘:
    {
      "title": "제목",
      "content": "HTML 형식을 포함한 본문 (이미지 태그나 스타일 포함 가능)",
      "labels": ["카테고리1", "카테고리2"],
      "design_advice": "오늘 블로그 분위기에 어울리는 디자인 조언"
    }
    조건: 아주 창의적이고 유익한 글이어야 해.
    """
    response = model.generate_content(prompt)
    data = json.loads(response.text.replace('```json', '').replace('```', ''))

    # 4. 블로거 API 인증 (손 움직이기)
    # 깃허브 액션에서는 매번 새로 인증해야 하므로 CLIENT_JSON_STR 활용
    creds_data = json.loads(CLIENT_JSON_STR)
    creds = Credentials.from_authorized_user_info(creds_data)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    
    service = build('blogger', 'v3', credentials=creds)

    # 5. 블로그 게시글 올리기
    post_body = {
        'title': data['title'],
        'content': data['content'],
        'labels': data['labels']
    }
    
    request = service.posts().insert(blogId=BLOG_ID, body=post_body)
    result = request.execute()
    print(f"성공적으로 게시됨: {result.get('url')}")

if __name__ == "__main__":
    run_agent()
