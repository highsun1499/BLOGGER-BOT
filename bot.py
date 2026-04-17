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
BLOG_ID = os.environ.get("BLOGGER_ID")
BLOGGER_TOKEN_JSON = os.environ.get("BLOGGER_TOKEN")

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemma-4-31b')

def get_blogger_service():
    token_dict = json.loads(BLOGGER_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(token_dict)
    return build('blogger', 'v3', credentials=creds)

def get_last_posted_nvidia_url(service):
    """블로그에서 가장 최근에 쓴 글 1개를 가져와 원본 출처(NVIDIA URL)를 찾습니다."""
    try:
        # 내 블로그의 가장 최근 게시물 1개만 가져옴
        posts = service.posts().list(blogId=BLOG_ID, maxResults=1).execute()
        items = posts.get('items',[])
        
        if not items:
            return None # 블로그에 글이 아예 하나도 없는 최초 상태
            
        latest_post_content = items[0].get('content', '')
        
        # HTML 태그 변형을 고려한 정규표현식으로 URL 추출 (큰따옴표, 작은따옴표 모두 대응)
        match = re.search(r"<strong>🔗 원본 출처:</strong> <a href=['\"](.*?)['\"]", latest_post_content)
        if match:
            return match.group(1)
            
    except Exception as e:
        print(f"최근 블로그 글 가져오기 에러: {e}")
    return None

def get_target_nvidia_urls(last_url, max_urls=3):
    """사이트맵을 예전 순(1, 2, 3...)으로 탐색하여 새로 작성할 URL 목록을 가져옵니다."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    index_url = "https://blogs.nvidia.com/sitemap_index.xml"
    res = requests.get(index_url, headers=headers)
    soup = BeautifulSoup(res.content, 'xml')
    
    sitemaps =[loc.text for loc in soup.find_all('loc') if 'post-sitemap' in loc.text]
    
    # post-sitemap 숫자순으로 오름차순 정렬 (post-sitemap.xml -> 1, post-sitemap2.xml -> 2)
    def extract_sitemap_number(url):
        match = re.search(r'post-sitemap(\d*)\.xml', url)
        if match:
            num = match.group(1)
            return int(num) if num else 1
        return 1 
    
    sorted_sitemaps = sorted(sitemaps, key=extract_sitemap_number, reverse=False)
    
    target_urls =[]
    # last_url이 없으면(최초 실행) 처음부터 바로 긁어오기 시작
    found_last = False if last_url else True 
    
    for sitemap in sorted_sitemaps:
        if len(target_urls) >= max_urls:
            break
            
        print(f"📄 사이트맵 탐색 중: {sitemap}")
        res_site = requests.get(sitemap, headers=headers)
        soup_site = BeautifulSoup(res_site.content, 'xml')
        
        # 예전 글부터 순방향으로 가져와야 하므로 reversed() 없이 그대로 가져옴
        urls =[loc.text for loc in soup_site.find_all('loc') if '/blog/' in loc.text]
        
        for url in urls:
            if len(target_urls) >= max_urls: # 목표치(3개)를 채웠으면 멈춤
                break
                
            if not found_last:
                # 사이트맵을 훑다가, 지난번에 마지막으로 쓴 URL을 발견하면!
                if url == last_url:
                    found_last = True # 그 다음 URL부터 수집하도록 스위치 ON
                continue
            
            # 스위치가 ON 상태라면 수집 목록에 추가
            target_urls.append(url)
            
    return target_urls

def scrape_nvidia_post(url):
    """NVIDIA 블로그 글의 텍스트 본문을 스크래핑합니다."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.content, 'html.parser')
    
    article_content = soup.find('article')
    if not article_content:
        return ""
    
    paragraphs = article_content.find_all('p')
    return "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])

def generate_blog_post_with_gemini(original_text, url):
    """Gemini API를 활용해 블로그 콘텐츠를 재가공합니다."""
    prompt = f"""
    당신은 한국 주식 및 코인 투자자들에게 AI 산업 트렌드와 투자 인사이트를 제공하는 전문 블로거입니다.
    아래 제공된 '엔비디아 블로그 원문(영어)'을 읽고, 다음의 조건들에 맞추어 한국어로 블로그 글을 작성해주세요.

    [조건]
    1. 글 길이는 **최소 2000자에서 최대 3000자** 사이를 엄격하게 지켜주세요.
    2. 한국 주식 투자자들이 매력을 느끼고 관심 가져할 만한 포인트(예: 수혜주 예측, AI 시장 전망, 주가에 미치는 영향 등)를 추가해서 요약 및 분석해주세요.
    3. 첫 번째 줄에는 반드시 블로그 포스팅의 '가장 매력적이고 어그로를 끌 수 있는 제목'을 적어주세요. (제목: 이라는 단어는 빼고 작성)
    4. 두 번째 줄부터 본문을 작성하며, 가독성을 위해 적절한 HTML 태그 (<h2>, <b>, <br> 등)를 섞어서 문단을 나눠주세요.

    [원문 내용]
    {original_text[:25000]}
    """
    
    response = model.generate_content(prompt)
    if not response.text:
        return None, None
        
    lines = response.text.strip().split('\n')
    title = lines[0].replace('#', '').strip()
    content = '\n'.join(lines[1:]).strip()
    
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
    
    print("1. 내 블로그에서 마지막으로 작성한 원본 링크 확인 중...")
    last_url = get_last_posted_nvidia_url(service)
    
    if last_url:
        print(f"🎯 마지막으로 포스팅한 URL: {last_url}")
    else:
        print("💡 블로그에 작성된 포스트가 감지되지 않아 가장 오래된 첫 번째 글부터 시작합니다.")
        
    print("\n2. 사이트맵에서 새로 포스팅할 타겟 URL 수집 중...")
    target_urls = get_target_nvidia_urls(last_url, max_urls=3) # 하루에 3개 작성
    
    if not target_urls:
        print("더 이상 포스팅할 새로운 글이 존재하지 않습니다.")
        return
        
    for url in target_urls:
        print(f"\n--- 🚀 다음 URL 진행 중: {url} ---")
            
        print("글 스크래핑 중...")
        text = scrape_nvidia_post(url)
        if not text:
            print("텍스트를 불러오지 못했습니다. 건너뜁니다.")
            continue
            
        print("Gemini API로 분석 및 글 작성 중...")
        title, new_content = generate_blog_post_with_gemini(text, url)
        
        if not title or not new_content:
            print("Gemini 글 생성 실패. 건너뜁니다.")
            continue
            
        print(f"생성된 제목: {title}")
        
        print("Blogger에 포스팅 중...")
        post_to_blogger(service, title, new_content)

if __name__ == "__main__":
    main()
