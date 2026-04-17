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
    """블로그에서 가장 최근에 쓴 글 1개를 가져와 원본 출처(NVIDIA URL)를 찾습니다."""
    try:
        # 내 블로그의 가장 최근 게시물 1개만 가져옴
        posts = service.posts().list(blogId=BLOGGER_ID, maxResults=1).execute()
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
    당신은 한국 투자자들에게 투자 인사이트를 제공하는 전문 블로거입니다.
    아래 제공된 '블로그 원문(영어)'을 읽고, 다음의 조건들에 맞추어 한국어로 블로그 글을 작성해주세요.

    [조건]
    1. 글 길이는 **최소 2000자에서 최대 3000자** 사이를 엄격하게 지켜주세요.
    2. 글의 구성 : [요약 + 분석 + 장점 3개 + 단점 3개 + 최종 3줄 요약 + 출처 링크]
    3. 아래 참고 예시를 참고하세요

    [참고 예시]
    엔비디아 주가 상승의 숨겨진 치트키, 'NVLink'가 도대체 무엇이길래? (ft. AI 반도체 투자 인사이트)
    <h2>🚀 엔비디아 블로그 원문 요약 : NVLink의 등장과 슈퍼컴퓨터의 진화</h2>
    <b>"세상에서 가장 빠른 컴퓨터를 가능하게 하는 기술, NVLink"</b><br><br>
    엔비디아(NVIDIA) 공식 블로그에 게재된 'NVLink란 무엇인가?'(What Is NVLink?) 포스팅에 따르면, 미국 에너지부(DOE)는 100 페타플롭스 이상의 성능을 자랑하는 차세대 슈퍼컴퓨터 '서밋(Summit)'과 '시에라(Sierra)' 구축 계획을 발표했습니다. 그리고 이 거대한 시스템의 뼈대가 되는 핵심 기술이 바로 엔비디아가 독자 개발한 <b>'NVLink(엔비링크)'</b>입니다.<br><br>
    GPU는 수천 개의 코어를 탑재하여 CPU보다 무려 10배 이상 빠르게 방대한 데이터를 처리할 수 있는 능력을 갖추고 있습니다. 하지만 여기서 치명적인 병목 현상(Bottleneck)이 발생합니다. 기존 데스크톱이나 노트북, 심지어 당시의 주류 슈퍼컴퓨터들도 GPU와 CPU, 또는 GPU 간을 연결하기 위해 'PCI Express(PCIe)'라는 범용 호환 규격을 사용했는데, GPU의 연산 속도는 비약적으로 빨라진 반면 데이터를 공급해 주는 파이프라인의 전송 속도가 이를 도무지 따라가지 못했던 것입니다.<br><br>
    이러한 데이터 가뭄 문제를 해결하기 위해 등장한 것이 바로 <b>세계 최초의 고속 GPU 인터커넥트 통신 구조인 NVLink</b>입니다. 엔비디아는 이 기술을 "마치 미국 로스앤젤레스의 고속도로를 4차선에서 20차선으로 확장하는 것"에 비유합니다. NVLink를 활용하면 GPU와 CPU, 또는 여러 GPU 사이의 데이터 이동 속도를 기존 정체된 도로(PCIe) 대비 <b>5배에서 최대 12배까지</b> 끌어올릴 수 있습니다. 또한 데이터 전송 과정의 에너지 효율성 및 서버 설계의 유연성까지 대폭 증대되어, 궁극적으로 1 퀸틸리언(100경) 번의 부동소수점 연산을 1초 만에 수행하는 엑사스케일(Exascale) 컴퓨팅 시대를 여는 핵심 열쇠로 묘사되고 있습니다.
    <h2>💡 요약 분석 : 투자자 관점에서 바라본 NVLink의 진정한 가치</h2>
    국내 서학개미 및 주식, 코인 투자자들이 단순히 '엔비디아 GPU 성능이 좋다'를 넘어 <b>'인터커넥트(연결) 기술'</b>에 지대한 관심을 가져야 하는 이유가 바로 여기에 있습니다. AI 시장이 폭발적으로 개화하며 챗GPT(ChatGPT)와 같은 거대언어모델(LLM)을 훈련시키기 위해 기하급수적으로 늘어난 파라미터(매개변수)와 데이터가 필요해졌습니다. 이제는 GPU 1개로 처리할 수 있는 시대가 아니며, 최소 수천에서 수만 개의 GPU를 연결하여 마치 '단 하나의 거대한 두뇌'처럼 작동시키는 <b>'클러스터링(Clustering)과 네트워킹 역량'</b>이 AI 기업의 명운을 좌우하게 되었습니다.<br><br>
    경쟁사인 AMD나 인텔(Intel)이 아무리 온전한 단일 칩셋 자체의 연산 성능을 엔비디아 성능과 엇비슷하게 끌어올린다고 하더라도, 수만 개의 칩을 묶었을 때 데이터 체증 없이 최고의 효율 교환을 달성하게 해주는 <b>'NVLink'라는 굳건한 해자(Moat)</b>를 넘지 못하면 데이터센터 시장에서 유미의한 점유율을 뺏어올 수 없습니다. 즉, NVLink는 엔비디아가 단순 반도체 칩 메이커를 넘어 고마진을 달성할 수 있는 '플랫폼 생태계'의 독점적 지위를 유지하게 방어해 주는 가장 강력하고 날카로운 무기입니다.
    <h2>✅ 압도적인 우위, NVLink의 3가지 장점</h2>
    <b>1. 폭발적인 초고속 데이터 전송 (병목현상 파괴)</b><br>
    PCIe의 길고 좁은 한계를 박살 내며 데이터 이동 속도를 최대 12배까지 향상시킵니다. 이는 AI 학습 시 필연적으로 발생하는 데이터 지연(Latency) 문제를 깨끗하게 해결하여, 값비싼 수천만 원짜리 GPU가 데이터를 받지 못해 대기하며 놀고 있는 시간(Idle time)을 없애고 100% 풀가동 성능을 발휘하도록 돕습니다.<br><br>
    <b>2. 대규모 데이터센터를 위한 에너지 전력 효율성</b><br>
    통신 데이터 전송량이 방대해지면 필연적으로 막대한 전력 소모 및 발열이 뒤따릅니다. 그러나 NVLink는 설계 기반부터 슈퍼컴퓨터와 엔터프라이즈 데이터센터 환경을 철저히 겨냥하여 만들어졌기 때문에, 기존 PCIe 구조보다 훨씬 더 적은 에너지 비중으로 더 많은 데이터를 보낼 수 있는 높은 에너지 효율성을 자랑합니다. (클라이언트의 막대한 전기세 유지비 절감에 직결됨)<br><br>
    <b>3. 서버 설계의 유연성과 확장성의 극대화</b><br>
    과거 지정된 메인보드 등 정해진 규격 안에서만 서버를 조립해야 하는 한계를 벗어나, NVLink를 통해 CPU, GPU 간 토폴로지를 훨씬 다이렉트하고 유연한 방식으로 엮을 수 있게 되었습니다. 빅테크 기업들이 자신들의 개별 AI 훈련 목적에 맞게 '맞춤형 커스텀 서버 아키텍처'를 마음껏 스케일업(Scale-up) 할 수 있는 판을 깔아줍니다.
    <h2>⚠️ 투자자가 반드시 확인해야 할 NVLink의 3가지 단점 (리스크)</h2>
    <b>1. 강력한 '벤더 락인 (Vendor Lock-in)'에 따른 업계 반발 우려</b><br>
    NVLink는 온전히 엔비디아의 생태계에만 폐쇄적으로 최적화된 독자(Proprietary) 규격입니다. 즉, 고객이 엔비디아 통신망 시스템에 한 번 발을 들이면 타사 칩이나 오픈 소스 스위치 등 외부 장비와 혼용하기가 매우 어려워집니다. 이는 장기적으로 지쳐버린 반(反) 엔비디아 연합 진영(AMD, UALink, 오픈 이더넷 연합 등)이 생존의 위협을 느껴 자체적인 연합 통신망 표준을 제시하며 저항하게 만드는 거대한 반발력을 낳고 있습니다.<br><br>
    <b>2. 상상을 초월하는 초기 인프라 구축 매몰 비용</b><br>
    NVSwitch와 최고 사양의 NVLink망을 서버 랙당 꽉꽉 채워 넣은 최상위 데이터센터를 구축하는 비용은 입이 떡 벌어지는 스케일입니다. 만약 향후 글로벌 침체 등 매크로 악재가 터져 빅테크들의 AI 설비 투자(CAPEX) 심리가 급격하게 얼어붙는다면, 이 값비싸고 무거운 인프라 장비 채택률부터 가파르게 둔화되어 주가에 타격을 줄 리스크가 상존합니다.<br><br>
    <b>3. 성능에 수반되는 극한의 전력 밀도 및 냉각 부담</b><br>
    데이터 전송당 효율은 뛰어나다고 할지라도, 워낙 조밀한 위치에 막대한 연산 장비가 붙어 초고속으로 데이터를 뿜어내다 보니 '초고밀도 발열'을 유발합니다. 공랭식(바람)의 한계를 이미 넘어섰기 때문에, 비용이 비싸고 유지관리가 까다로운 수랭식 냉각(액침 냉각 시스템 등) 인프라가 함께 받쳐주지 않으면 무용지물이 될 수 있다는 운영상의 거대한 진입 장벽이 발생합니다.
    <h2>🏆 최종 평가 : 반도체 전쟁에서 엔비디아가 여전히 매력적인 이유</h2>
    투자의 관점에서 NVLink는 단순 부품이 아니라 <b>"우리가 왜 여전히 지배적인 1등인지"</b>를 역설하는 엔비디아의 자존심이자 심장입니다. 일반 대중들은 엔비디아를 단순히 연산 칩을 깎는 설계사로 생각하지만, 기관과 스마트머니(Smart money)는 엔비디아가 이미 소프트웨어 독점망(CUDA)과 이 하드웨어 통신망(NVLink)을 양손에 쥐고 성벽을 높게 쌓은 '초거대 플랫폼' 기업이라는 본질에 압도적인 프리미엄 밸류를 부여하고 있습니다.<br><br>
    경쟁사들의 파상공세 속에서도, 다수의 빅테크 1군 기업들이 쫓고 있는 궁극의 AGI(범용인공지능) 시대를 열람하기 위해 가장 절실하게 필요한 것은 거대한 클러스터링을 손실률 0%에 수렴하며 묶어내는 '네트워킹 파워'입니다. 따라서 엔비디아라는 거대 생태계 본체뿐만 아니라, 엔비디아가 깔아놓은 이 강력한 통신망이 원활히 돌아갈 수 있도록 뒤에서 받쳐주는 'HBM(고대역폭 메모리)', '수랭식 인프라 인테리어' 관련 밸류체인은 국내외 증시에서 향후로도 가장 폭발력 있는 메가 트렌드 투자처라 판단합니다.
    <br><hr><br>
    <h2>📌 오늘의 핵심 내용 3줄 요약</h2>
    <b>1.</b> 엔비디아의 NVLink는 기존 범용 연결 기술의 한계를 박살내며 5배~12배 폭발적인 데이터 전송 속도를 구현한 독자적인 고속 인터커넥트 구조다.<br>
    <b>2.</b> AI 시대에는 단일 칩 성능보다 수만 개의 칩을 묶어 연산시키는 능력이 핵심이므로, 뛰어난 연결성을 보장하는 NVLink는 경쟁사가 넘볼 수 없는 엔비디아의 강력한 1위 해자(Moat)로 작용하고 있다.<br>
    <b>3.</b> 막대한 인프라 비용과 치명적 발열 문제라는 단점이 존재하나, 투자자들은 이를 해결하기 위해 나서는 '액체 냉각 시스템' 및 '차세대 메모리' 등 파생 수혜 섹터에서 텐배거의 기회를 노려야 한다.
    <br><br><br>출처 : https://blogs.nvidia.com/blog/what-is-nvlink/

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
    
    content_with_link = f"{content}<br><br><hr><br><strong>🔗 원본 출처:</strong> <a href='{url}' target='_blank'>{url}</a>"
    return title, content_with_link

def post_to_blogger(service, title, content):
    """가공된 글을 블로거에 업로드합니다."""
    body = {
        "kind": "blogger#post",
        "title": title,
        "content": content
    }
    
    result = service.posts().insert(blogId=BLOGGER_ID, body=body, isDraft=False).execute()
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
    target_urls = get_target_nvidia_urls(last_url, max_urls=1) # 하루에 1개 작성
    
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
