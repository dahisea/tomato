import os
import sys
import json
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from ebooklib import epub
from typing import Dict, List, Optional

CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "default_format": "epub",
    "default_threads": 4,
    "api_timeout": 10,
    "show_colors": True,
    "save_cover": True
}

DISCLAIMER = "本工具仅供学习交流"
os.makedirs('download', exist_ok=True)

# 加载配置
def load_config() -> Dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()

config = load_config()

# 颜色
class Colors:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    PURPLE = "\033[35m"
    CYAN = "\033[36m"

    @staticmethod
    def wrap(text: str, color: str) -> str:
        return f"{color}{text}{Colors.RESET}" if config["show_colors"] else text

# 全局会话
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
})

book_info_cache = {}

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '', name).strip()[:100]

def extract_book_id_from_url(url: str) -> Optional[str]:
    url = url.strip()
    m = re.search(r'(\d{10,20})', url)
    return m.group(1) if m else None

def get_book_metadata(book_id: str) -> Dict:
    if book_id in book_info_cache:
        return book_info_cache[book_id]

    metadata = {
        "book_name": f"未知书名_{book_id}",
        "author": "未知作者",
        "summary": "无简介",
        "cover_url": "",
        "category": "未知类型",
        "status": "未知状态",
        "word_count": "未知字数",
        "readers": "未知在读人数"
    }

    try:
        api_url = "https://toma.jam.cz.eu.org.cdn.cloudflare.net/info/"
        params = {
            "aid": "1967",
            "iid": "1",
            "version_code": "999",
            "book_id": book_id
        }
        resp = session.get(api_url, params=params, timeout=config["api_timeout"])
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == 0 and data.get("data"):
            book_data = data["data"][0]
            metadata["book_name"] = book_data.get("book_name", metadata["book_name"])
            metadata["author"] = book_data.get("author", metadata["author"])
            metadata["summary"] = book_data.get("abstract", metadata["summary"])
            metadata["cover_url"] = book_data.get("bookshelf_thumb_url") or book_data.get("thumb_url", "")
            metadata["category"] = book_data.get("category", metadata["category"])
            metadata["status"] = "连载中" if book_data.get("update_status") == "1" else "已完结"
            if book_data.get("word_count"):
                metadata["word_count"] = f"{book_data['word_count']/10000:.1f}万字"
            metadata["readers"] = book_data.get("sub_info", metadata["readers"])

            if metadata["book_name"].startswith("《") and metadata["book_name"].endswith("》"):
                metadata["book_name"] = metadata["book_name"][1:-1]
        else:
            print(Colors.wrap("API获取Text信息失败", Colors.RED))
            sys.exit(1)

    except Exception as e:
        print(f"{Colors.wrap('获取Text信息失败', Colors.RED)}: {str(e)}")
        sys.exit(1)

    book_info_cache[book_id] = metadata
    return metadata

def get_chapter_list(book_id: str) -> List[Dict]:
    url = f"https://toma.jam.cz.eu.org.cdn.cloudflare.net/directory?bookId={book_id}"
    try:
        resp = session.get(url, timeout=config["api_timeout"])
        data = resp.json()
        if data.get("code") == 0:
            chapters = []
            for volume in data["data"]["chapterListWithVolume"]:
                for chapter in volume:
                    chapters.append({"title": chapter.get("title", ""), "item_id": chapter["itemId"]})
            return chapters
    except Exception as e:
        print(f"{Colors.wrap('获取章节列表失败', Colors.RED)}: {str(e)}")
    sys.exit(1)

def download_chapter(item_id: str) -> Optional[str]:
    url = "https://toma.jam.cz.eu.org.cdn.cloudflare.net/down/"
    params = {"item_id": item_id, "novelsdk_aid": "638505", "sdk_type": "4"}
    try:
        resp = session.post(url, params=params, timeout=(5, 15))
        data = resp.json()
        if data.get("code") == 0 and "content" in data.get("data", {}):
            content = data["data"]["content"]
            clean_content = content.replace('</p>', '\n').replace('&quot;', '"').replace('&amp;', '&')
            return re.sub(r'<p idx="\d+"\u003e', '', clean_content).strip()
    except:
        pass
    return None

def build_txt(metadata: Dict, chapters: List[Dict], output_path: str):
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("第0章：声明\n\n" + DISCLAIMER + "\n\n" + "-"*50 + "\n\n")
        f.write(f"《{metadata['book_name']}》\n作者：{metadata['author']}\n类型：{metadata['category']} | 状态：{metadata['status']}\n\n简介：{metadata['summary']}\n\n" + "="*50 + "\n\n")
        for i, chap in enumerate(chapters):
            f.write(f"第{i+1}章：{chap['title']}\n\n{chap['content']}\n\n" + "-"*50 + "\n\n")
        f.write(f"第{len(chapters)+1}章：再次声明\n\n" + DISCLAIMER + "\n\n" + "="*50 + "\n")
    print(f"{Colors.wrap('TXT生成成功', Colors.GREEN)}：{output_path}")

def build_epub(metadata: Dict, chapters: List[Dict], output_path: str):
    book = epub.EpubBook()
    book.set_title(metadata["book_name"])
    book.add_author(metadata["author"])
    book.set_language("zh-CN")
    book.add_metadata('DC', 'description', metadata["summary"])

    if config["save_cover"] and metadata["cover_url"]:
        try:
            resp = session.get(metadata["cover_url"], timeout=10)
            if resp.status_code == 200:
                book.set_cover("cover.jpg", resp.content)
        except:
            pass

    epub_chapters = []
    spine = ["nav"]

    disclaim_start = epub.EpubHtml(title="声明", file_name="disclaim_start.xhtml", lang="zh-CN")
    disclaim_start.content = f"<h1>声明</h1><p>{DISCLAIMER}</p>"
    book.add_item(disclaim_start)
    epub_chapters.append(disclaim_start)
    spine.append(disclaim_start)

    intro = epub.EpubHtml(title="Text信息", file_name="intro.xhtml", lang="zh-CN")
    intro.content = "<h1>《{}》</h1><p><strong>作者：</strong>{}</p><p><strong>类型：</strong>{} | <strong>状态：</strong>{}</p><p><strong>简介：</strong>{}</p>".format(
    metadata['book_name'],
    metadata['author'],
    metadata['category'],
    metadata['status'],
    metadata['summary'].replace('\n', '<br/>')
)
    book.add_item(intro)
    epub_chapters.append(intro)
    spine.append(intro)

    for i, chap in enumerate(chapters):
        if not chap["content"]:
            continue
        c = epub.EpubHtml(title=f"第{i+1}章：{chap['title']}", file_name=f"chap_{i+1}.xhtml", lang="zh-CN")
        c.content = "<h2>第{}章：{}</h2><p>{}</p>".format(
    i + 1,
    chap['title'],
    chap['content'].replace('\n', '<br/>')
)
        book.add_item(c)
        epub_chapters.append(c)
        spine.append(c)

    disclaim_end = epub.EpubHtml(title="再次声明", file_name="disclaim_end.xhtml", lang="zh-CN")
    disclaim_end.content = f"<h1>再次声明</h1><p>{DISCLAIMER}</p>"
    book.add_item(disclaim_end)
    epub_chapters.append(disclaim_end)
    spine.append(disclaim_end)

    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    epub.write_epub(output_path, book, {})
    print(f"{Colors.wrap('EPUB生成成功', Colors.GREEN)}：{output_path}")

def download_novel(book_id: str):
    print(f"\n{Colors.wrap('===== Text信息 =====', Colors.BLUE)}")
    metadata = get_book_metadata(book_id)
    print(f"书名：{Colors.wrap(metadata['book_name'], Colors.PURPLE)}")
    print(f"作者：{metadata['author']}")
    print(f"类型：{metadata['category']} | 状态：{metadata['status']}")

    chapters = get_chapter_list(book_id)
    if not chapters:
        print(f"{Colors.wrap('未找到章节列表', Colors.RED)}")
        return
    total_chapters = len(chapters)
    print(f"\n{Colors.wrap(f'发现 {total_chapters} 个章节，开始下载...', Colors.GREEN)}")

    chap_contents = []
    with ThreadPoolExecutor(max_workers=config["default_threads"]) as executor:
        futures = {executor.submit(download_chapter, chap["item_id"]): i for i, chap in enumerate(chapters)}
        for future in tqdm(as_completed(futures), total=total_chapters, desc="下载进度"):
            idx = futures[future]
            try:
                content = future.result()
                chap_contents.append({"title": chapters[idx]["title"], "content": content or f"【章节 {idx+1} 下载失败】"})
            except Exception as e:
                chap_contents.append({"title": chapters[idx]["title"], "content": f"【章节 {idx+1} 下载失败：{str(e)}】"})

    chap_contents.sort(key=lambda x: chapters.index(next(c for c in chapters if c["title"] == x["title"])))
    fname = f"{sanitize_filename(metadata['book_name'])}-{sanitize_filename(metadata['author'])}"
    if '未知' in fname:
        fname += f"_{book_id}"
    output_path = os.path.join('download', f"{fname}.{config['default_format']}")

    if config["default_format"] == "epub":
        build_epub(metadata, chap_contents, output_path)
    else:
        build_txt(metadata, chap_contents, output_path)

    print(f"\n{Colors.wrap('下载完成！', Colors.GREEN)}文件保存至：{os.path.abspath(output_path)}")

def main():
    if len(sys.argv) < 2:
        print("用法: python downloader.py <TextID或链接> [--epub|--txt] [--threads=N]")
        sys.exit(1)

    book_id = extract_book_id_from_url(sys.argv[1])
    if not book_id:
        print(Colors.wrap("无法解析TextID，请检查输入", Colors.RED))
        sys.exit(1)

    for arg in sys.argv[2:]:
        if arg == "--txt":
            config["default_format"] = "txt"
        elif arg == "--epub":
            config["default_format"] = "epub"
        elif arg.startswith("--threads="):
            try:
                config["default_threads"] = int(arg.split("=")[1])
            except:
                pass

    download_novel(book_id)

if __name__ == "__main__":
    main()
