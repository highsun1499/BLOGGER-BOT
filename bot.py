import os
import re
import json
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# 환경 변수에서 값 가져오기
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BLOG_ID = os.environ.get("BLOG_ID")
BLOGGER_TOKEN_JSON = os.environ.get("BLOGGER_TOKEN")

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # 빠르고 텍스트 생성에 우수한 1.5 flash 모델

def get_blogger_service():
    """Blogger API 서비스 객체 생성"""
    token_dict = json.loads(BLOGGER_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(token_dict)
    service = build('blogger', 'v3', credentials=creds)
    return service

def get_latest_nvidia_urls(max_urls=3):
    """NVIDIA 사이트맵에서 가장 최근 포스트 URL들을 가져옵니다."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 1. 사이트맵 인덱스 가져오기
    index_url = "https://blogs.nvidia.com/sitemap_index.xml"
    res = requests.get(index_url, headers=headers)
    soup = BeautifulSoup(res.content, 'xml')
    
    # post-sitemap이 들어간 최신 사이트맵 URL 찾기 (보통 숫자가 젤 큰 것이 최신)
    sitemaps =[loc.text for loc in soup.find_all('loc') if 'post-sitemap' in loc.text]
    latest_sitemap = sorted(sitemaps, reverse=True)[0]
    
    # 2. 최신 사이트맵에서 포스트 URL 가져오기
    res_site = requests.get(latest_sitemap, headers=headers)
    soup_site = BeautifulSoup(res_site.content, 'xml')
    
    post_urls =[loc.text for loc in soup_site.find_all('loc')]
    
    # 사이트맵은 보통 최신 글이 맨 아래에 있거나 마지막 `<loc>`에 추가됨
    # 혹시 모르니 가장 마지막 5개를 뒤집어서 최신순으로 가져옴
    return list(reversed(post_urls))[:max_urls]

def is_duplicate(service, url):
    """최근 블로거 포스트 10개를 검색하여 해당 URL이 이미 포스팅 되었는지 확인 (중복 체크)"""
    try:
        posts = service.posts().list(blogId=BLOG_ID, maxResults=10).execute()
        for post in posts.get('items',[]):
            if url in post.get('content', ''):
                return True
    except Exception as e:
        print(f"중복 체크 중 에러 발생: {e}")
    return False

def scrape_nvidia_post(url):
    """NVIDIA 블로그 글의 텍스트 본문을 스크래핑합니다."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.content, 'html.parser')
    
    # 본문이 들어있는 주요 태그 찾기 (엔비디아 블로그 구조에 맞춤)
    article_content = soup.find('article')
    if not article_content:
        return ""
    
    paragraphs = article_content.find_all('p')
    text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
    return text

def generate_blog_post_with_gemini(original_text, url):
    """Gemini API를 활용해 블로그 콘텐츠를 재가공합니다."""
    prompt = f"""
    당신은 한국 주식 및 코인 투자자들에게 AI 산업 트렌드와 투자 인사이트를 제공하는 전문 블로거입니다.
    아래 제공된 '엔비디아 블로그 원문(영어)'을 읽고, 다음의 조건들에 맞추어 한국어로 블로그 글을 작성해주세요.

    [조건]
    1. 글 길이는 **최소 2000자에서 최대 3000자** 사이를 엄격하게 지켜주세요. 상세하게 분석 내용을 덧붙여도 좋습니다.
    2. 한국 주식 투자자들이 매력을 느끼고 관심 가져할 만한 포인트(예: 수혜주 예측, AI 시장 전망, 주가에 미치는 영향 등)를 추가해서 요약 및 분석해주세요.
    3. 첫 번째 줄에는 반드시 블로그 포스팅의 '가장 매력적이고 어그로를 끌 수 있는 제목'을 적어주세요. (제목: 이라는 단어는 빼고 작성)
    4. 두 번째 줄부터 본문을 작성하며, 가독성을 위해 적절한 HTML 태그 (<h2>, <b>, <br> 등)를 섞어서 문단을 나눠주세요.

    [원문 내용]
    {original_text[:25000]} # 너무 길어 토큰을 초과하는 것을 방지
    """
    
    response = model.generate_content(prompt)
    if not response.text:
        return None, None
        
    lines = response.text.strip().split('\n')
    title = lines[0].replace('#', '').strip() # 첫 줄은 제목
    content = '\n'.join(lines[1:]).strip() # 나머지는 본문
    
    # 맨 아래에 원본 링크 추가
    content_with_link = f"{content}<br><br><hr><br><strong>🔗 원본 출처:</strong> <a href='{url}' target='_blank'>{url}</a>"
    
    return title, content_with_link

def post_to_blogger(service, title, content):
    """가공된 글을 블로거에 업로드합니다."""
    body = {
        "kind": "blogger#post",
        "title": title,
        "content": content
    }
    
    result = service.posts().insert(blogId=BLOG_ID, body=body, isDraft=False).execute()
    print(f"✅ 블로그 포스팅 성공: {result.get('url')}")

def main():
    service = get_blogger_service()
    
    print("1. 최신 NVIDIA 블로그 URL 수집 중...")
    latest_urls = get_latest_nvidia_urls(max_urls=3)
    
    for url in latest_urls:
        print(f"\n확인 중인 URL: {url}")
        
        # 중복 체크
        if is_duplicate(service, url):
            print("⚠️ 이미 블로그에 작성된 원본 링크입니다. 포스팅을 건너뜁니다.")
            continue
            
        print("2. 글 스크래핑 중...")
        text = scrape_nvidia_post(url)
        if not text:
            print("텍스트를 불러오지 못했습니다. 건너뜁니다.")
            continue
            
        print("3. Gemini API로 글 작성 중...")
        title, new_content = generate_blog_post_with_gemini(text, url)
        
        if not title or not new_content:
            print("Gemini 글 생성 실패. 건너뜁니다.")
            continue
            
        print(f"생성된 제목: {title}")
        
        print("4. Blogger에 포스팅 중...")
        post_to_blogger(service, title, new_content)
        
        # 한 번 실행에 하나씩만 올리고 싶다면 여기서 break 처리.
        # 여러 개가 안 올라가 있었다면 전부 올리려면 break 삭제
        break 

if __name__ == "__main__":
    main()
