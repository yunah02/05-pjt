import time

from selenium.webdriver.support.ui import WebDriverWait

from crawler import (POST_ITEM, WAIT_SEC, _build_driver, _open_community,
                     _search_top_company)

driver = _build_driver(headless=False)
try:
    wait = WebDriverWait(driver, WAIT_SEC)
    _search_top_company(driver, wait, '삼성')
    _open_community(driver, wait)
    time.sleep(4)

    html = driver.execute_script("""
        const posts = [...document.querySelectorAll(arguments[0])].slice(0, 8);
        return posts.map((p, i) => {
            const c = p.cloneNode(true);
            c.querySelectorAll('svg, img').forEach(e => e.remove());
            return '<!-- POST ' + i + ' -->\\n' + c.outerHTML;
        }).join('\\n\\n');
    """, POST_ITEM)

    with open('debug_posts.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('저장 완료: debug_posts.html, 길이 =', len(html))
    driver.save_screenshot('debug_posts.png')
finally:
    driver.quit()