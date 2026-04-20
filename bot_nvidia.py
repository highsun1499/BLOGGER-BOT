import os
import re
import json
import requests
from bs4 import BeautifulSoup
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# 환경 변수에서 값 가져오기
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BLOGGER_ID = os.environ.get("BLOGGER_ID")
BLOGGER_TOKEN = os.environ.get("BLOGGER_TOKEN")

# Gemini API 설정
client = genai.Client(api_key=GEMINI_API_KEY)

def get_blogger_service():
    token_dict = json.loads(BLOGGER_TOKEN)
    creds = Credentials.from_authorized_user_info(token_dict)
    return build('blogger', 'v3', credentials=creds)

def get_last_posted_nvidia_url(service):
    """블로그에서 가장 최근에 쓴 글을 가져와 출처(NVIDIA URL)를 찾습니다."""
    try:
        # 🚨 수정사항 1 : maxResults는 반드시 1이어야 합니다! (가장 최신 글 1개만 확인하면 됨)
        posts = service.posts().list(blogId=BLOGGER_ID, maxResults=1).execute()
        items = posts.get('items',[])
        
        if not items:
            return None # 블로그에 글이 아예 하나도 없는 최초 상태
            
        latest_post_content = items[0].get('content', '')
        
        # 🚨 수정사항 2 : 구글 블로거가 태그 순서를 바꿔도 무조건 찾아내는 강력한 '유연한 정규식' 도입
        match = re.search(r"🔗 출처:.*?href=['\"](.*?)['\"]", latest_post_content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
            
    except Exception as e:
        print(f"최근 블로그 글 가져오기 에러: {e}")
    return None

def get_target_nvidia_urls(last_url, max_urls=1):
    """사이트맵을 예전 순(1, 2, 3...)으로 탐색하여 새로 작성할 URL 목록을 가져옵니다."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    index_url = "https://blogs.nvidia.com/sitemap_index.xml"
    res = requests.get(index_url, headers=headers)
    soup = BeautifulSoup(res.content, 'xml')
    
    sitemaps =[loc.text for loc in soup.find_all('loc') if 'post-sitemap' in loc.text]
    
    def extract_sitemap_number(url):
        match = re.search(r'post-sitemap(\d*)\.xml', url)
        if match:
            num = match.group(1)
            return int(num) if num else 1
        return 1 
    
    sorted_sitemaps = sorted(sitemaps, key=extract_sitemap_number, reverse=False)
    
    target_urls =[]
    found_last = False if last_url else True 
    
    for sitemap in sorted_sitemaps:
        if len(target_urls) >= max_urls:
            break
            
        print(f"📄 사이트맵 탐색 중: {sitemap}")
        res_site = requests.get(sitemap, headers=headers)
        soup_site = BeautifulSoup(res_site.content, 'xml')
        
        urls =[loc.text for loc in soup_site.find_all('loc') if '/blog/' in loc.text]
        
        for url in urls:
            if len(target_urls) >= max_urls: 
                break
                
            if not found_last:
                # 🚨 수정사항 3 : url 끝에 슬래시('/') 유무 때문에 불일치하는 것을 막기 위한 안전장치 (rstrip)
                if url.rstrip('/') == last_url.rstrip('/'):
                    found_last = True # 다음 URL부터 수집하도록 스위치 ON
                continue
            
            target_urls.append(url)
            
    return target_urls

def scrape_nvidia_post(url):
    """NVIDIA 블로그 글의 텍스트 본문을 스크래핑합니다. (죽은 링크 방어막 적용)"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # timeout=10 을 추가하여 10초 이상 응답이 없으면 포기합니다.
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status() # 404(페이지 없음)나 500 에러를 잡기 위한 방어막
        
        soup = BeautifulSoup(res.content, 'html.parser')
        
        article_content = soup.find('article')
        if not article_content:
            return ""
        
        paragraphs = article_content.find_all('p')
        return "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
    except Exception as e:
        # 에러가 발생해도 봇이 죽지 않고 빈칸("")을 반환해 자연스럽게 넘어가도록 함
        print(f"⚠️ 불량 링크 감지 (원문 스크래핑 실패): {e}")
        return ""

def generate_blog_post_with_gemini(original_text, url):
    """Gemini API를 활용해 블로그 콘텐츠를 재가공합니다."""
    prompt = f"""
    당신은 한국 투자자들에게 투자 인사이트를 제공하는 전문 블로거입니다.
    아래 제공된 '블로그 원문(영어)'을 읽고, 다음의 조건들에 맞추어 한국어로 블로그 글을 작성해주세요.

    [조건]
    1. 글 길이는 **최소 2000자에서 최대 3000자** 사이를 엄격하게 지켜주세요.
    2. 글의 구성 : [요약 + 분석 + 장점 3개 + 단점 3개 + 최종 3줄 요약]
    3. 아래 참고 예시를 참고하세요.
    4. 글 제목은 100자 이내로 작성하세요. 

    [참고 예시]
    엔비디아 주가 상승의 숨겨진 치트키, 'NVLink'가 도대체 무엇이길래? (ft. AI 반도체 투자 인사이트)
    <h2>📝 요약</h2>
    <b>"세상에서 가장 빠른 컴퓨터를 가능하게 하는 기술, NVLink"</b>
    엔비디아(NVIDIA) 공식 블로그에 게재된 포스팅에 따르면, 기존 슈퍼컴퓨터의 치명적인 데이터 병목 현상(Bottleneck)을 해결하기 위해 <b>'NVLink(엔비링크)'</b>라는 독자 기술이 등장했습니다. 기존 범용 규격인 PCIe 구조를 쓸 때보다 데이터 이동 속도를 <b>5배에서 최대 12배까지</b> 끌어올리며, 엑사스케일(Exascale) 컴퓨팅 시대를 여는 핵심 열쇠로 묘사되고 있습니다.
    <br><br><h2>🔍 분석</h2>
    AI 시장이 폭발적으로 개화하면서 이제는 최소 수천에서 수만 개의 GPU를 연결하여 단 하나의 거대한 두뇌처럼 작동시키는 <b>'클러스터링과 네트워킹 역량'</b>이 기업의 명운을 좌우하게 되었습니다. 경쟁사들이 칩 단일 성능을 따라잡더라도, 최고 효율의 통신망인 <b>'NVLink'라는 굳건한 해자(Moat)</b>를 넘지 못하면 데이터센터 점유율을 뺏어올 수 없습니다. 이는 엔비디아가 고마진 생태계를 독점 유지하게 방어해 주는 날카로운 무기입니다.
    <br><br><h2>📈 장점</h2>
    <b>1. 폭발적인 초고속 데이터 전송 :
    </b> AI 학습 시 발생하는 데이터 지연(Latency)을 해결해 값비싼 GPU가 100% 풀가동 성능을 발휘하도록 돕습니다.<br>
    <b>2. 대규모 데이터센터 전력 효율성 :
    </b> 슈퍼컴퓨터 환경을 겨냥해 설계되어, 기존 방식보다 훨씬 적은 에너지로 더 많은 데이터를 보냅니다.<br>
    <b>3. 서버 설계의 유연성 :
    </b> 빅테크 기업들이 자신들의 개별 AI 훈련 목적에 맞게 맞춤형 서버를 마음껏 스케일업 할 수 있는 판을 깔아줍니다.
    <br><br><h2>📉 단점 :</h2>
    <b>1. 강력한 벤더 락인(Vendor Lock-in) :
    </b> 엔비디아 생태계에만 폐쇄적으로 최적화되어, 타사 칩이나 외부 장비와 혼용하기 매우 극악하며 반(反) 엔비디아 연합의 저항을 낳고 있습니다.<br>
    <b>2. 막대한 매몰 비용 :
    </b> 시스템 도입 비용이 상상을 초월하여 향후 AI 설비 투자(CAPEX) 심리가 얼어붙을 경우 주가 타격의 리스크가 존재합니다.<br>
    <b>3. 극한의 냉각 부담 :
    </b> 초고밀도 발열을 유발하므로 유지관리가 까다로운 수랭식 냉각(액침 냉각 등) 인프라가 강제된다는 진입 장벽이 발생합니다.
    <br><br><h2>🎯 정리 :</h2>
    투자의 관점에서 NVLink는 "엔비디아가 왜 여전히 지배적인 1등인지"를 역설하는 심장입니다. 스마트머니는 엔비디아가 이미 소프트웨어 독점망(CUDA)과 하드웨어 통신망(NVLink)을 양손에 쥐고 성벽을 쌓은 '초거대 플랫폼'이라는 본질에 압도적인 밸류를 부여하고 있습니다. 따라서 이를 원활히 돌아가게 받쳐주는 HBM, 수랭식 인프라 관련 기업들이 향후 훌륭한 투자처가 될 수 있습니다.
    <br><br><hr><br>
    <h2>📌 핵심 내용 3줄 요약</h2>
    <b>1.</b> 엔비디아의 NVLink는 기존 범용 연결 기술의 한계를 박살내며 5배~12배 폭발적인 데이터 전송 속도를 구현한 독자적인 고속 인터커넥트 구조다.<br>
    <b>2.</b> AI 시대에는 단일 칩 성능보다 수만 개의 칩을 묶어 연산시키는 능력이 핵심이므로, 뛰어난 연결성을 보장하는 NVLink는 경쟁사가 넘볼 수 없는 엔비디아의 강력한 1위 해자(Moat)로 작용하고 있다.<br>
    <b>3.</b> 막대한 인프라 비용과 치명적 발열 문제라는 단점이 존재하나, 투자자들은 이를 해결하기 위해 나서는 '액체 냉각 시스템' 및 '차세대 메모리' 등 파생 수혜 섹터에서 텐배거의 기회를 노려야 한다.

    [원문 내용]
    {original_text[:9876543210]}
    """
    
    response = client.models.generate_content(
        model='gemma-4-31b-it', # 사용할 모델 이름 여기에 입력
        contents=prompt
    )
    
    if not response.text:
        return None, None
        
    lines = response.text.strip().split('\n')
    title = lines[0].replace('#', '').strip()
    content = '\n'.join(lines[1:]).strip()
    content = content.replace('$\\rightarrow$', '→').replace('$\rightarrow$', '→')
    content_with_link = f"{content}<br><br><hr><br><strong>🔗 출처:</strong> <a href='{url}' target='_blank'>{url}</a>"
    return title, content_with_link

def post_to_blogger(service, title, content, original_url):
    """가공된 글을 블로거에 업로드하며, 주소(URL)를 원본과 완벽히 일치시킵니다."""
    try:
        slug = original_url.strip('/').split('/')[-1]
        temp_title = slug.replace('-', ' ')
        
        # 1. 🚨 주소를 영구 고정하려면 무조건 '공개(isDraft=False)' 상태로 최초 발행해야 합니다!
        body = {
            "kind": "blogger#post",
            "title": temp_title,
            "content": content,
            "labels":["엔비디아"] 
        }
        inserted_post = service.posts().insert(blogId=BLOGGER_ID, body=body, isDraft=False).execute()
        post_id = inserted_post.get('id')
        
    except Exception as e:
        print(f"❌ 임시 발행 중 에러 발생: {e}")
        return # 발행 자체가 실패했으므로 여기서 함수 종료

    try:
        # 2. 발행이 완료되어 주소가 고정되었으니, 제미니의 진짜 한국어 제목으로 업데이트(patch)를 시도합니다.
        update_body = {
            "title": title
        }
        result = service.posts().patch(blogId=BLOGGER_ID, postId=post_id, body=update_body).execute()
        
        print(f"✅ 블로그 포스팅 성공 (주소 일치 완료): {result.get('url')}")
        
    except Exception as e:
        # 3. 🚨 덮어쓰기 에러 시, 이상한 영문 제목 글이 대중에게 노출되는 것을 막기 위해 '즉시 강제 삭제'
        print(f"❌ 제목 덮어쓰기 에러 발생! 라이브 방지를 위해 해당 글을 즉시 삭제합니다: {e}")
        service.posts().delete(blogId=BLOGGER_ID, postId=post_id).execute()
        print("➡️ 글이 삭제되었으므로 내일 다시 이 글부터 포스팅을 재도전합니다.")

def get_all_posted_urls(service):
    """[신규] 블로그에 이미 작성된 모든 포스팅의 원본 URL들을 긁어모아 '집합(Set)'으로 만듭니다."""
    print("🔍[Sweep 모드] 블로그 내 전체 게시글을 조회하여 누락된 글을 찾습니다...")
    posted_urls = set()
    next_page_token = None
    
    while True:
        try:
            # 🚨 타겟 라벨("엔비디아")이 달린 글만 싹 다 훑어옵니다.
            request = service.posts().list(blogId=BLOGGER_ID, maxResults=9876543210, labels="엔비디아", pageToken=next_page_token)
            res = request.execute()
            
            for item in res.get('items',[]):
                match = re.search(r"🔗 출처:.*?href=['\"](.*?)['\"]", item.get('content', ''), re.IGNORECASE | re.DOTALL)
                if match:
                    posted_urls.add(match.group(1).rstrip('/'))
                    
            next_page_token = res.get('nextPageToken')
            if not next_page_token:
                break
        except Exception as e:
            print(f"전체 목록 조회 중 에러: {e}")
            break
            
    print(f"✅ 총 {len(posted_urls)}개의 정상 발행된 문서 링크 목록을 확보했습니다.")
    return posted_urls

def get_missing_target_urls(posted_urls, max_urls=9876543210):
    """[신규] 사이트맵을 처음부터 점검하며, 블로그에 아직 없는 URL만 골라옵니다."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    index_url = "https://blogs.nvidia.com/sitemap_index.xml"
    res = requests.get(index_url, headers=headers)
    soup = BeautifulSoup(res.content, 'xml')
    
    sitemaps =[loc.text for loc in soup.find_all('loc') if 'post-sitemap' in loc.text]
    
    def extract_sitemap_number(url):
        match = re.search(r'post-sitemap(\d*)\.xml', url)
        if match:
            num = match.group(1)
            return int(num) if num else 1
        return 1 
        
    sorted_sitemaps = sorted(sitemaps, key=extract_sitemap_number, reverse=False)
    target_urls =[]
    
    for sitemap in sorted_sitemaps:
        if len(target_urls) >= max_urls:
            break
            
        res_site = requests.get(sitemap, headers=headers)
        soup_site = BeautifulSoup(res_site.content, 'xml')
        urls =[loc.text for loc in soup_site.find_all('loc') if '/blog/' in loc.text]
        
        for url in urls:
            if len(target_urls) >= max_urls:
                break
            
            clean_url = url.rstrip('/')
            # 블로그(posted_urls)에 없는 링크라면 타겟으로 포획!
            if clean_url not in posted_urls:
                target_urls.append(url)
                
    return target_urls

def main():
    service = get_blogger_service()
    
    print("1. 내 블로그에서 마지막으로 작성한 원본 링크 확인 중...")
    last_url = get_last_posted_nvidia_url(service)
    
    if last_url:
        print(f"🎯 마지막으로 포스팅한 URL: {last_url}")
    else:
        print("💡 블로그에 작성된 포스트가 없거나 파악되지 않아 1번째 글부터 시작합니다.")
        
    print("\n2. 사이트맵에서 새로 포스팅할 타겟 URL 수집 중...")
    target_urls = get_target_nvidia_urls(last_url, max_urls=9876543210) 
    
    # 🌟 [여기서부터 새로 추가된 핵심 로직입니다] 🌟
    if not target_urls:
        print("💡 앞방향으로 새로 쓸 글이 없습니다. 최신 글 끝까지 도달했습니다!")
        print("🔄 [전체 누락 점검 모드] 역주행을 가동하여 빠진 구멍을 찾아냅니다.")
        
        # 블로그에 있는 수천 개의 글 주소를 순식간에 다 불러옴 (API 1~2번 소모로 매우 빠름)
        posted_urls = get_all_posted_urls(service)
        # 처음부터 훑으면서 블로그에 없는 글만 넉넉히(15개) 가져옴
        target_urls = get_missing_target_urls(posted_urls, max_urls=9876543210) 
        
        if not target_urls:
            print("🏁 더 이상 포스팅할 빈 구멍도 없습니다! 완벽하게 모든 글이 동기화되었습니다.")
            return

    # 🌟 [동일 유지] 아래부터는 기존 포스팅 로직과 동일 🌟
    posted_count = 0 
    
    for url in target_urls:
        if posted_count >= 1: # 하루 목표치 달성 시 퇴근
            print("✅ 일일 목표 포스팅 1개 작성을 달성하여 봇을 종료합니다.")
            break
            
        print(f"\n--- 🚀 다음 URL 진행 중: {url} ---")
            
        print("글 스크래핑 중...")
        text = scrape_nvidia_post(url)
        if not text:
            print("➡️ 텍스트를 불러오지 못했습니다. 불량 링크로 간주하고 다음 글로 넘어갑니다.")
            continue
            
        print("Gemini API로 분석 및 글 작성 중...")
        title, new_content = generate_blog_post_with_gemini(text, url)
        
        if not title or not new_content:
            print("➡️ Gemini 글 생성 실패. 다음 글로 넘어갑니다.")
            continue
            
        print(f"생성된 제목: {title}")
        
        print("Blogger에 포스팅 중...")
        post_to_blogger(service, title, new_content, url)
        
        posted_count += 1 

if __name__ == "__main__":
    main()
