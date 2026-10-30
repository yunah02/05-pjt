import time

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
import re

BASE_URL = 'https://www.tossinvest.com/'
SEARCH_INPUT = "input[placeholder]"                      
COMMUNITY_TAB = "//*[self::a or self::button][normalize-space()='커뮤니티']"
POST_ITEM = 'div[data-section-name="커뮤니티__게시글"]'   
POST_TEXT = 'span[style*="--fold-after-lines"]'           

TARGET_COUNT = 20
WAIT_SEC = 10
TOP_RESULT_JS = """
const items = [...document.querySelectorAll('span, div, p')].filter(e => {
    if (e.children.length !== 0) return false;
    if (!/^[0-9A-Z]{6}$/.test(e.textContent.trim())) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
});
if (!items.length) return null;
items.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
return items[0];
"""

class CompanyNotFound(Exception):
    """검색 결과가 없을 때"""


class NoComments(Exception):
    """댓글이 하나도 없을 때"""


def _build_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--window-size=1400,1000')
    options.add_argument('--lang=ko-KR')
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )


def _open_search_modal(driver):
    """검색 모달을 열고 검색 input을 반환. '/' 키 → 실패 시 검색창 클릭."""
    ActionChains(driver).send_keys('/').perform()
    try:
        return WebDriverWait(driver, 4).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, SEARCH_INPUT)))
    except TimeoutException:
        pass

    opener = driver.find_element(By.XPATH, "//*[contains(text(),'눌러 검색')]")
    driver.execute_script('arguments[0].click();', opener)
    return WebDriverWait(driver, WAIT_SEC).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, SEARCH_INPUT)))


def _click_top_result(driver):
    """검색 결과가 안정될 때까지 기다린 뒤 최상단 결과를 클릭."""
    last_code = None
    for _ in range(10):                     
        try:
            el = driver.execute_script(TOP_RESULT_JS)
            code = el.text.strip() if el else None
            if code and code == last_code:  
                driver.execute_script('arguments[0].click();', el)
                return True
            last_code = code
        except StaleElementReferenceException:
            last_code = None
        time.sleep(0.7)
    return False


def _search_top_company(driver, wait, query):
    driver.get(BASE_URL)
    time.sleep(5)

    try:
        box = _open_search_modal(driver)
    except Exception:
        driver.save_screenshot('debug_search.png')
        raise

    box.send_keys(Keys.CONTROL, 'a')          
    box.send_keys(Keys.DELETE)
    box.send_keys(query)

    if not _click_top_result(driver):          
        driver.save_screenshot('debug_search_result.png')
        raise CompanyNotFound(query)

    try:
        wait.until(EC.url_contains('/stocks/'))
    except TimeoutException:
        driver.save_screenshot('debug_search_result.png')
        raise CompanyNotFound(query)

def _get_stock_code(driver):
    """현재 URL에서 종목코드 추출. 예: .../stocks/A005930/order -> A005930"""
    m = re.search(r'/stocks/([A-Za-z0-9]+)', driver.current_url)
    return m.group(1) if m else None


def _open_community(driver, wait):
    code = _get_stock_code(driver)
    if not code:
        raise CompanyNotFound('종목코드를 찾지 못했습니다.')
    driver.get(f'{BASE_URL}stocks/{code}/community')
    time.sleep(3)

def _collect_comments(driver, wait, target=TARGET_COUNT, notify=None):
    """notify가 있으면 댓글을 하나 모을 때마다 notify('collect', n=개수)를 호출한다."""
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, POST_ITEM)))
    except TimeoutException:
        raise NoComments()

    seen_ids, comments = set(), []
    stalled = 0                                 

    for _ in range(30):
        before = len(comments)

        for post in driver.find_elements(By.CSS_SELECTOR, POST_ITEM):
            try:
                post_id = post.get_attribute('data-post-anchor-id')
                if post_id in seen_ids:
                    continue
                span = post.find_element(By.CSS_SELECTOR, POST_TEXT)
                text = (span.get_attribute('textContent') or '').strip()
            except (NoSuchElementException, StaleElementReferenceException):
                continue                         
            if text:
                seen_ids.add(post_id)
                comments.append(text)
                if notify:
                    notify('collect', n=len(comments))
                if len(comments) >= target:
                    return comments

        stalled = stalled + 1 if len(comments) == before else 0
        if stalled >= 3:                          
            break

        posts = driver.find_elements(By.CSS_SELECTOR, POST_ITEM)
        if posts:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'end'});", posts[-1])
            except StaleElementReferenceException:
                pass
        time.sleep(1)

    return comments[:target]


def _get_stock_name(driver):
    """제목이 '가격 | 종목명' 형태로 바뀔 때까지 기다린 뒤 종목명 추출."""
    try:
        WebDriverWait(driver, WAIT_SEC).until(lambda d: '|' in d.title)
    except TimeoutException:
        return '알 수 없음'
    return driver.title.split('|')[-1].strip()


def crawl_comments(query, headless=True, on_progress=None):
    """(실제종목명, 댓글 리스트) 반환. 실패 시 CompanyNotFound / NoComments.

    on_progress(event, **data): 진행 상황 알림용 콜백 (실시간 스트리밍에 사용)
      'driver'   브라우저 시작
      'search'   검색어 입력
      'selected' 최상단 종목 클릭 완료 (code=종목코드)
      'stock'    종목명 확인 (name=종목명)
      'collect'  댓글 수집 중 (n=지금까지 모은 개수)
    """
    notify = on_progress or (lambda *args, **kwargs: None)
    notify('driver')
    driver = _build_driver(headless)
    try:
        wait = WebDriverWait(driver, WAIT_SEC)
        notify('search')
        _search_top_company(driver, wait, query)
        notify('selected', code=_get_stock_code(driver))
        _open_community(driver, wait)
        stock_name = _get_stock_name(driver)
        notify('stock', name=stock_name)
        comments = _collect_comments(driver, wait, notify=notify)
        if not comments:
            raise NoComments()
        return stock_name, comments
    finally:
        driver.quit()        
        
if __name__ == '__main__':
    name, items = crawl_comments('삼성', headless=False)
    print('종목명:', name, '| 수집 개수:', len(items))
    for i, c in enumerate(items):
        print(i, f'({len(c)}자)', c)