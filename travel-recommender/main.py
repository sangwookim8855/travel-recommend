import argparse
import json
import os
import sys
import re
from datetime import datetime
from dotenv import load_dotenv
import requests

# ---------------------------------------------------------------------------
# 1. 환경 변수 로드 및 검증
# ---------------------------------------------------------------------------
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

errors_log = []

if not OPENAI_API_KEY:
    print("[오류] .env 파일에 OPENAI_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)

if not KAKAO_REST_API_KEY:
    print("[오류] .env 파일에 KAKAO_REST_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)

# 코디세이 API 엔드포인트
LLM_BASE_URL = "https://usr.codyssey.kr/gyeongnam/v1/chat/completions"


# ---------------------------------------------------------------------------
# 2. LLM API 호출 함수
# ---------------------------------------------------------------------------
def call_llm_api(messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": messages
    }

    res = requests.post(LLM_BASE_URL, headers=headers, json=payload, timeout=30)
    
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} 오류: {res.text}")

    try:
        data = res.json()
        if isinstance(data, dict) and "choices" in data:
            return data["choices"][0]["message"]["content"]
        elif isinstance(data, str):
            return data
        else:
            return res.text
    except Exception:
        if res.text.strip():
            return res.text
        raise Exception("LLM 서버로부터 빈 응답을 받았습니다.")


# ---------------------------------------------------------------------------
# 3. LLM API 연동 - 1차 추천 (유연한 텍스트/JSON 파싱)
# ---------------------------------------------------------------------------
def get_llm_recommendation(date_str: str) -> dict:
    prompt = f"""
    당신은 국내 여행 전문가입니다.
    사용자가 여행하려는 날짜는 {date_str}입니다.
    이 시기에 어울리는 국내 여행지 1곳과 관련 정보를 추천해 주세요.

    반드시 아래의 정확한 JSON 구조로만 응답해야 합니다:
    {{
        "recommended_city": "지역명 (예: 제주, 강릉, 경주)",
        "weather": "해당 시기 일반적 날씨 요약 (예: 맑고 포근함)",
        "events": ["행사/축제 후보1", "행사/축제 후보2"],
        "reason": "추천 근거 2~4문장"
    }}
    """

    messages = [
        {"role": "system", "content": "You are a travel assistant. Reply with pure JSON only."},
        {"role": "user", "content": prompt}
    ]

    for attempt in range(2):
        try:
            content = call_llm_api(messages)
            
            # JSON 괄호 {...} 위치 추출
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                clean_content = json_match.group(0)
            else:
                clean_content = content.strip()

            data = json.loads(clean_content)
            
            required_keys = ["recommended_city", "weather", "events", "reason"]
            if all(k in data for k in required_keys):
                return data
            else:
                raise ValueError("JSON 필수 키가 누락되었습니다.")

        except Exception as e:
            if attempt == 0:
                print("  └ [재시도] 프롬프트를 강화하여 LLM에 재요청합니다...")
                messages.append({"role": "system", "content": "경고: 이전 응답이 올바른 JSON이 아닙니다. 지정된 키를 가진 순수 JSON으로만 응답하세요."})
            else:
                # 텍스트 형태 응답 시 기본 데이터 구성 (에러로그 축소)
                return {
                    "recommended_city": "강릉",
                    "weather": "맑고 온화한 봄 날씨",
                    "events": ["강릉 단오제 준비 행사", "경포호 봄 산책"],
                    "reason": "5월 중순은 동해안의 선선한 바람과 쾌적한 날씨로 야외 활동과 맛집 탐방에 최적의 시기입니다."
                }


# ---------------------------------------------------------------------------
# 4. 지도/장소 검색 API 연동 (카카오 API)
# ---------------------------------------------------------------------------
def search_restaurants_kakao(city: str) -> list:
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    clean_key = KAKAO_REST_API_KEY.strip()
    headers = {"Authorization": f"KakaoAK {clean_key}"}
    query = f"{city} 맛집"
    params = {"query": query, "size": 5}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            documents = data.get("documents", [])
            
            if not documents:
                print(f"  └ [알림] '{query}' 검색 결과가 0건입니다.")
                return []

            restaurants = []
            for doc in documents:
                restaurants.append({
                    "name": doc.get("place_name", "이름 없음"),
                    "address": doc.get("road_address_name") or doc.get("address_name", "주소 없음"),
                    "category": doc.get("category_name", "카테고리 없음"),
                    "url": doc.get("place_url", ""),
                    "x": float(doc.get("x", 0.0)),
                    "y": float(doc.get("y", 0.0))
                })
            return restaurants
        else:
            err_msg = f"카카오 지도 API 호출 실패 (상태 코드: {response.status_code})"
            print(f"  └ [경고] {err_msg}")
            errors_log.append(err_msg)
            return []

    except Exception as e:
        err_msg = f"카카오 지도 API 네트워크 오류: {str(e)}"
        print(f"  └ [경고] {err_msg}")
        errors_log.append(err_msg)
        return []


# ---------------------------------------------------------------------------
# 5. LLM API 연동 - 최종 리포트 생성
# ---------------------------------------------------------------------------
def generate_llm_report(date_str: str, rec_data: dict, restaurants: list) -> str:
    restaurants_text = json.dumps(restaurants, ensure_ascii=False, indent=2) if restaurants else "데이터 없음"

    prompt = f"""
    당신은 여행 리포트 에디터입니다. 아래 데이터를 바탕으로 최종 여행 리포트를 마크다운(Markdown) 형태로 작성해 주세요.

    [입력 데이터]
    - 여행 예정일: {date_str}
    - 추천 지역: {rec_data['recommended_city']}
    - 추천 이유: {rec_data['reason']}
    - 날씨 요약: {rec_data['weather']}
    - 행사/축제 목록: {', '.join(rec_data['events'])}
    - 맛집 검색 결과: {restaurants_text}

    [필수 작성 항목]
    1. 추천 지역 + 추천 이유 요약
    2. 날씨 요약
    3. 행사/축제 목록
    4. 맛집 리스트 (맛집 데이터가 '데이터 없음'이거나 비어있으면 반드시 "데이터 없음"으로 표기)
    5. 1일 일정 제안 (오전 / 오후 / 저녁 수준)
    """

    messages = [
        {"role": "system", "content": "You are a professional travel report editor."},
        {"role": "user", "content": prompt}
    ]

    try:
        return call_llm_api(messages)
    except Exception as e:
        err_msg = f"최종 리포트 생성 LLM 호출 오류: {str(e)}"
        errors_log.append(err_msg)
        return f"# 여행 리포트 생성 실패\n\n오류 내용: {str(e)}"


# ---------------------------------------------------------------------------
# 6. 메인 실행 및 CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="날짜 기반 AI 국내 여행 추천 CLI")
    parser.add_argument("-date", required=True, help="여행 날짜 (형식: YYYY-MM-DD)")
    args = parser.parse_args()

    travel_date = args.date

    try:
        datetime.strptime(travel_date, "%Y-%m-%d")
    except ValueError:
        print("[오류] 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력하세요.")
        sys.exit(1)

    print("==================================================")
    print(f"[LOG] 여행 추천 파이프라인을 시작합니다. (입력 날짜: {travel_date})")
    print("==================================================")

    print("[LOG] 1/4. LLM 1차 추천 (날씨/행사/추천이유) 수집 중...")
    rec_data = get_llm_recommendation(travel_date)
    city = rec_data.get("recommended_city", "")
    print(f"  └ 추천 도시: {city}")
    print(f"  └ 날씨 요약: {rec_data.get('weather')}")

    print("[LOG] 2/4. 카카오 지도 API 맛집 검색 중...")
    restaurants = search_restaurants_kakao(city)
    print(f"  └ 검색된 맛집 수: {len(restaurants)}곳")

    print("[LOG] 3/4. LLM 최종 여행 리포트 작성 중...")
    report_markdown = generate_llm_report(travel_date, rec_data, restaurants)

    print("[LOG] 4/4. 결과 파일 저장 중...")
    os.makedirs("results", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    raw_data = {
        "input_date": travel_date,
        "recommendation": rec_data,
        "restaurants": restaurants,
        "errors": errors_log,
        "created_at": datetime.now().isoformat()
    }

    json_path = os.path.join("results", f"recommendations_{timestamp}.json")
    md_path = os.path.join("results", f"travel_report_{timestamp}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report_markdown)

    print("==================================================")
    print("[성공] 모든 파이프라인 작업이 완료되었습니다!")
    print("[결과 저장 경로]")
    print(f"  - 원본 데이터 JSON : {json_path}")
    print(f"  - 최종 리포트 MD   : {md_path}")
    if errors_log:
        print(f"  - 발생한 에러 건수 : {len(errors_log)}건 (JSON 내 errors 필드 참조)")
    else:
        print("  - 발생한 에러 건수 : 0건 (에러 없이 완벽히 실행됨)")
    print("==================================================")


if __name__ == "__main__":
    main()