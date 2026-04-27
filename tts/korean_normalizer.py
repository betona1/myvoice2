"""
tts/korean_normalizer.py
XTTS v2 한국어 텍스트 정규화
- 숫자 → 한국어 올바른 읽기 (901→구백일, 2025→이천이십오)
- 숫자+단위 → 올바른 읽기 (2663조3000억→이천육백육십삼조삼천억)
- 소수+% → 올바른 읽기 (4.2%→사 점 이 퍼센트)
- 영어 약어 → 한글 발음 변환 (GDP→지디피, AI→에이아이)
- 특수기호 → 한국어 발음 변환
- 텍스트 분할 (250자 버그 회피)
"""

import re

# 특수기호 → 한국어 발음 변환 (%, 괄호는 별도 처리)
SYMBOL_MAP = {
    '@': ' 골뱅이 ',
    '#': ' 샵 ',
    '$': ' 달러 ',
    '&': ' 앤드 ',
    '*': ' 별표 ',
    '+': ' 플러스 ',
    '=': ' 이퀄 ',
    '/': ' 슬래시 ',
    '\\': ' 역슬래시 ',
    '|': ' 바 ',
    '~': ' 물결 ',
    '^': ' 캐럿 ',
    '<': ' 작다 ',
    '>': ' 크다 ',
    '[': ' ',
    ']': ' ',
    '{': ' ',
    '}': ' ',
    ':': ' ',
    ';': ' ',
    '"': ' ',
    "'": ' ',
    '-': ' ',
    '_': ' ',
}

# 영문 알파벳 → 한글 발음 (약어 읽기용)
ALPHA_KOREAN = {
    'A': '에이', 'B': '비', 'C': '씨', 'D': '디', 'E': '이',
    'F': '에프', 'G': '지', 'H': '에이치', 'I': '아이', 'J': '제이',
    'K': '케이', 'L': '엘', 'M': '엠', 'N': '엔', 'O': '오',
    'P': '피', 'Q': '큐', 'R': '알', 'S': '에스', 'T': '티',
    'U': '유', 'V': '브이', 'W': '더블유', 'X': '엑스', 'Y': '와이',
    'Z': '제트',
}

# 자주 쓰이는 영어 약어 → 한글 발음 (통째 매핑)
ABBREV_KOREAN = {
    'GDP': '지디피', 'GNP': '지엔피', 'IMF': '아이엠에프',
    'AI': '에이아이', 'IT': '아이티', 'CEO': '씨이오',
    'UN': '유엔', 'USA': '유에스에이', 'EU': '이유',
    'OECD': '오이씨디', 'WHO': '더블유에이치오',
    'FIFA': '피파', 'NASA': '나사', 'NATO': '나토',
    'USB': '유에스비', 'CPU': '씨피유', 'GPU': '지피유',
    'RAM': '램', 'ROM': '롬', 'LED': '엘이디',
    'LCD': '엘씨디', 'TV': '티비', 'PC': '피씨',
    'SNS': '에스엔에스', 'URL': '유알엘', 'API': '에이피아이',
    'KBS': '케이비에스', 'MBC': '엠비씨', 'SBS': '에스비에스',
    'KTX': '케이티엑스', 'LG': '엘지', 'SK': '에스케이',
    'BMW': '비엠더블유', 'IBM': '아이비엠',
    'FTA': '에프티에이', 'PPT': '피피티', 'PDF': '피디에프',
    'OS': '오에스', 'IOT': '아이오티', 'VR': '브이알', 'AR': '에이알',
    'GPS': '지피에스', 'ATM': '에이티엠', 'MVP': '엠브이피',
    'DJ': '디제이', 'MC': '엠씨', 'PR': '피알',
    'ESG': '이에스지', 'ETF': '이티에프', 'IPO': '아이피오',
    'CPI': '씨피아이', 'PPI': '피피아이',
}


def _english_abbrev_to_korean(word: str) -> str:
    """영어 약어를 한글 발음으로 변환 (대문자 2글자 이상)"""
    upper = word.upper()
    # 통째 매핑에 있으면 바로 반환
    if upper in ABBREV_KOREAN:
        return ABBREV_KOREAN[upper]
    # 대문자 2~6글자 약어는 한 글자씩 변환
    if upper == word and 2 <= len(word) <= 6:
        return ''.join(ALPHA_KOREAN.get(ch, ch) for ch in upper)
    return word

# 한국어 숫자 (자릿수용 - 0은 건너뜀)
DIGIT_NAMES = ['', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
# 한국어 숫자 (개별 읽기용 - 소수점 이하)
DIGIT_READ = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
UNITS_SMALL = ['', '십', '백', '천']
UNITS_LARGE = ['', '만', '억', '조', '경']

# 고유어 수사 (시간/개수 읽기용: 1→한, 2→두, ... 20→스물)
NATIVE_KOREAN_NUMS = {
    1: '한', 2: '두', 3: '세', 4: '네', 5: '다섯',
    6: '여섯', 7: '일곱', 8: '여덟', 9: '아홉', 10: '열',
    11: '열한', 12: '열두', 13: '열세', 14: '열네', 15: '열다섯',
    16: '열여섯', 17: '열일곱', 18: '열여덟', 19: '열아홉', 20: '스물',
    21: '스물한', 22: '스물두', 23: '스물세', 24: '스물네',
}


def _number_to_native_korean(num_str: str) -> str:
    """숫자를 고유어 수사로 변환 (시간/개수용: 1→한, 10→열, 24→스물네)"""
    num = int(num_str)
    if num in NATIVE_KOREAN_NUMS:
        return NATIVE_KOREAN_NUMS[num]
    # 24 초과는 한자어 수사로 대체
    return _number_to_korean(num_str)


def _number_to_korean(num_str: str) -> str:
    """
    숫자를 한국어로 올바르게 읽기
    0→영, 10→십, 901→구백일, 2025→이천이십오, 39000→삼만구천
    """
    num_str = num_str.lstrip('0') or '0'

    if num_str == '0':
        return '영'

    result = ''
    # 4자리씩 끊어서 처리 (경/조/억/만/일)
    groups = []
    s = num_str
    while s:
        groups.append(s[-4:])
        s = s[:-4]
    groups.reverse()

    n_groups = len(groups)
    for i, group in enumerate(groups):
        group_val = int(group)
        if group_val == 0:
            continue

        group_str = _four_digits_to_korean(group_val)
        large_unit_idx = n_groups - 1 - i
        if large_unit_idx < len(UNITS_LARGE):
            large_unit = UNITS_LARGE[large_unit_idx]
        else:
            large_unit = ''

        result += group_str + large_unit

    return result if result else '영'


def _four_digits_to_korean(num: int) -> str:
    """4자리 이하 숫자를 한국어로 변환"""
    if num == 0:
        return ''

    result = ''
    s = str(num)
    length = len(s)

    for i, ch in enumerate(s):
        digit = int(ch)
        if digit == 0:
            continue
        unit_idx = length - 1 - i

        # '일'은 십/백/천 앞에서는 생략 (일십→십, 일백→백)
        if digit == 1 and unit_idx > 0:
            digit_str = ''
        else:
            digit_str = DIGIT_NAMES[digit]

        result += digit_str + UNITS_SMALL[unit_idx]

    return result


def _convert_decimal_str(text: str) -> str:
    """소수점 문자열 변환 (3.14 → 삼 점 일사)"""
    if '.' not in text:
        return _number_to_korean(text)
    parts = text.split('.')
    integer_part = _number_to_korean(parts[0]) if parts[0] else '영'
    decimal_part = ''.join(DIGIT_READ[int(d)] for d in parts[1] if d.isdigit())
    return integer_part + ' 점 ' + decimal_part


def normalize_korean_text(text: str) -> str:
    """
    XTTS v2용 한국어 텍스트 정규화
    - 숫자: 한국어 올바른 읽기
    - 영어: 그대로 유지
    - 특수기호: 한국어 발음으로 변환
    """
    # 앞뒤 공백 및 캐리지 리턴 제거
    text = text.strip()
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 제어문자, 이모지, 처리 불가 유니코드 제거 (한글/영문/숫자/기본문장부호/공백만 유지)
    text = re.sub(r'[^\uAC00-\uD7A3\u3131-\u318Ea-zA-Z0-9\s.,!?%@#$&*+=/\\|~^<>()\[\]{}\-_:;\'"\n]', '', text)

    # URL 제거
    text = re.sub(r'https?://\S+', '', text)

    # 이메일 → 읽기 변환
    text = re.sub(r'(\S+)@(\S+)\.(\S+)', r'\1 골뱅이 \2 점 \3', text)

    # 괄호 안 내용 추출 (괄호 제거) - (GDP) → GDP, [참고] → 참고
    text = re.sub(r'[(\[{](.*?)[)\]}]', r' \1 ', text)

    # 영어 약어 → 한글 발음 변환 (GDP→지디피, AI→에이아이)
    # \b는 한글 조사 앞에서 안 먹으므로 전후 문맥으로 판단
    text = re.sub(r'(?<![A-Za-z])([A-Z]{2,6})(?![a-zA-Z])', lambda m: _english_abbrev_to_korean(m.group(1)), text)

    # 쉼표 포함 숫자 → 쉼표 제거 (39,000 → 39000)
    text = re.sub(r'(\d{1,3}(?:,\d{3})+)', lambda m: m.group().replace(',', ''), text)

    # 년도 패턴 (2025년 → 이천이십오년, 2024년도 → 이천이십사년도)
    text = re.sub(r'(\d{4})(년도?)', lambda m: _number_to_korean(m.group(1)) + m.group(2), text)

    # 고유어 수사 단위 (시, 시간, 개, 명, 번, 살, 잔, 마리, 벌, 채, 대, 곳, 장)
    # 10시 → 열시, 3개 → 세개, 5명 → 다섯명
    text = re.sub(r'(\d+)(시간|시|개|명|번|살|잔|마리|벌|채|곳|장)',
                  lambda m: _number_to_native_korean(m.group(1)) + m.group(2), text)

    # 한자어 수사 단위 (월, 일, 분, 초, 원, 배, 위, 차, 호, 권, 회, 층, 편, 개월, 주년, 주일)
    # 12월 → 십이월, 25일 → 이십오일, 30분 → 삼십분
    text = re.sub(r'(\d+)(월|일|분|초|원|배|위|차|호|권|회|층|편|개월|주년|주일)',
                  lambda m: _number_to_korean(m.group(1)) + m.group(2), text)

    # 숫자+한국어 대단위 패턴 (2663조3000억 → 이천육백육십삼조삼천억)
    def _convert_num_with_unit(m):
        full = m.group()
        # 숫자+단위 쌍을 찾아서 각각 변환
        result = re.sub(r'(\d+)', lambda n: _number_to_korean(n.group()), full)
        return result
    text = re.sub(r'\d+[조억만천백십][\d조억만천백십]*', _convert_num_with_unit, text)

    # 소수+% 변환 (4.2% → 사 점 이 퍼센트, 1.0% → 일 점 영 퍼센트)
    text = re.sub(r'(\d+\.\d+)\s*%', lambda m: _convert_decimal_str(m.group(1)) + ' 퍼센트', text)

    # 정수+% 변환 (100% → 백 퍼센트)
    text = re.sub(r'(\d+)\s*%', lambda m: _number_to_korean(m.group(1)) + ' 퍼센트', text)

    # 버전 표기 (v2.0 → 버전 이 점 영)
    text = re.sub(r'[vV](\d+\.\d+)', lambda m: '버전 ' + _convert_decimal_str(m.group(1)), text)

    # 나머지 소수점 숫자 (3.14 → 삼 점 일사)
    text = re.sub(r'\d+\.\d+', lambda m: _convert_decimal_str(m.group()), text)

    # 나머지 정수 → 한국어
    text = re.sub(r'\d+', lambda m: _number_to_korean(m.group()), text)

    # 특수기호 → 한국어 발음 변환
    for symbol, pronunciation in SYMBOL_MAP.items():
        text = text.replace(symbol, pronunciation)

    # 남은 % 처리 (단독 %)
    text = text.replace('%', ' 퍼센트 ')

    # 남은 지원하지 않는 문자 제거 (한글, 영문, 기본 문장부호만 허용)
    text = re.sub(r'[^\uAC00-\uD7A3a-zA-Z\s.,!?]', ' ', text)

    # 연속 공백 정리
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def split_text_for_xtts(text: str, max_chars: int = 70) -> list[str]:
    """
    XTTS v2 입력 텍스트 분할
    - 250자 제한 버그 회피: 70자 이하로 분할 (러시아어 전환 방지)
    - 문장 단위 분할 우선
    - 긴 문장은 쉼표/조사/공백 기준 재분할
    """
    # 줄바꿈도 문장 구분자로 처리
    text = text.replace('\n', '. ')

    # 문장 분리 (마침표, 느낌표, 물음표 기준)
    sentences = re.split(r'(?<=[.!?])\s*', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            # 1차: 쉼표 기준으로 재분할
            sub_parts = re.split(r'(?<=,)\s*', sentence)
            for part in sub_parts:
                if len(part) > max_chars:
                    # 2차: 한국어 조사/어미 뒤 또는 공백 기준 강제 분할
                    # 한국어 특성상 공백이 자연스러운 분할점
                    words = part.split()
                    for word in words:
                        if len(current_chunk) + len(word) + 1 <= max_chars:
                            current_chunk += word + " "
                        else:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = word + " "
                elif len(current_chunk) + len(part) <= max_chars:
                    current_chunk += part + " "
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = part + " "
        elif len(current_chunk) + len(sentence) <= max_chars:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return [c for c in chunks if c]
