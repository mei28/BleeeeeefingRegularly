import datetime
import os

from dotenv import load_dotenv
from icecream import ic
from notion.block import (
    BulletedListBlock,
    CollectionViewBlock,
    PageBlock,
    SubheaderBlock,
    SubsubheaderBlock,
    TextBlock,
)
from notion.client import NotionClient
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# .envファイルを読み込み、環境変数として扱う
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path)
SLACK_TOKEN = os.environ.get("OAuth_Access_Token")
NOTION_TOKEN = os.environ.get("token_v2")
TOP_PAGE_URL = os.environ.get("top_page")

# 各種サービスに接続するインスタンス
slack_client = WebClient(token=SLACK_TOKEN)
notion_client = NotionClient(token_v2=NOTION_TOKEN)

channel = "#bleeeeeefing"

today = datetime.date.today()

# Bleeeeeefingのページを管理しているトップページ
top_page = notion_client.get_block(TOP_PAGE_URL)


def _str_to_date(date: str) -> datetime.date:
    """
    例) "20210214" → datetime.date(2021, 2, 14)
    """
    tdatetime = datetime.datetime.strptime(date, "%Y%m%d")
    return datetime.date(tdatetime.year, tdatetime.month, tdatetime.day)


def _title_contains_desired_date(child: PageBlock, target_date: datetime.date) -> bool:
    """
    child.titleは1週間を表す"20210212〜20210218"のような形式の文字列である。
    target_dateがこの週に含まれていればTrueを返す。
    """
    splitted_title = child.title.split("〜")
    if len(splitted_title) != 2:
        # "〜"で区切れなければ日付の形式をとったノートでないと判断して終了
        return False
    # タイトルに含まれている日付をdatetime.dateオブジェクトに変換
    week_begin, week_end = map(_str_to_date, splitted_title)
    if week_begin <= target_date <= week_end:
        # 該当する週のブロックである
        return True
    return False


def _to_pretty(content: list) -> list:
    """
    Slack投稿用に文字列を加工する
    """
    ret = []
    for line in content:
        # FIXME: Blockのクラスによって条件分岐する
        # SubheaderBlockとかBulletedListとか
        if line == "Done":
            # 見出し語はアスタリスクで囲う（Slackのボールド体はアスタリスク1つ）
            ret.append(f"*{line}*")
        elif line in ["TODO", "Doing", "Problems"]:
            # 同様に（2つ目以降の見出し語の前には改行を入れる）
            ret.append(f"\n*{line}*")
        else:
            # それ以外はリストの要素とする
            ret.append(f"・{line}")
    return ret


def _fetch_page_content_by_date(date: datetime.date) -> list:
    """
    引数で指定した日付のBleeeeeefingページの内容を取得する
    """
    # 今週のサブページを取得
    this_week = [
        child
        for child in top_page.children
        if _title_contains_desired_date(child, date)
    ][0]
    # 日毎のページを集約したコレクションを取得
    this_week_reports = [
        child
        for child in this_week.children
        if _title_contains_desired_date(child, date)
    ][0]
    # 今日の日付のページを取得
    filter_params = {
        "filters": [
            {
                "property": "title",
                "filter": {
                    "operator": "string_is",
                    "value": {
                        "type": "exact",
                        "value": date.strftime("%Y%m%d"),
                    },
                },
            }
        ],
        "operator": "and",
    }
    today_report = this_week_reports.collection.query(filter=filter_params)[0]
    # 本文を抜き出して
    content = [child.title for child in today_report.children]
    # 文字列を加工する
    content = _to_pretty(content)
    return content


def _fetch_weekly_summary_by_date(date: datetime.date) -> list:
    """
    引数で指定した日付を含んだ週のBleeeeeefingの内容を取得する
    """
    # 今週のサブページを取得
    this_week = [
        child
        for child in top_page.children
        if _title_contains_desired_date(child, date)
    ][0]
    # サマリーが書いてある行だけを抽出
    content = [
        child.title
        for child in this_week.children
        if type(child) not in [TextBlock, CollectionViewBlock]
    ]
    # 先頭の"Summary"という行を削除
    content = [line for line in content if line != "Summary"]
    # 文字列を加工する
    content = _to_pretty(content)
    # 先頭に今週の日付を挿入
    content.insert(0, this_week.title)
    return content


def post_to_slack(content: str) -> None:
    """
    Slackのbleeeeeefingチャンネルに投稿する
    Copied from https://github.com/slackapi/python-slack-sdk#sending-a-message-to-slack
    """
    try:
        response = slack_client.chat_postMessage(channel=channel, text=content)
        assert response["message"]["text"] == content
    except SlackApiError as e:
        # You will get a SlackApiError if "ok" is False
        assert e.response["ok"] is False
        assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'
        print(f"Got an error: {e.response['error']}")


def daily_bleeeeeefing() -> None:
    """
    日次報告
    """
    # 今日のBleeeeeefing内容
    contents = _fetch_page_content_by_date(today)
    # 先頭に今日の日付を挿入
    contents.insert(0, today.strftime("%Y/%m/%d"))
    # Slackに投稿
    content = "\n".join(contents)
    post_to_slack(content)


def weekly_bleeeeeefing() -> None:
    """
    週次報告
    """
    # 1日前までの1週間の報告なので、昨日の日付を指定
    yesterday = today - datetime.timedelta(days=1)
    # 今週のBleeeeeefing内容
    contents = _fetch_weekly_summary_by_date(yesterday)
    # Slackに投稿
    content = "\n".join(contents)
    post_to_slack(content)
    # 次週分のページをテンプレートから生成
    _make_weekly_from_template(today)


def _make_content(page, layout) -> None:
    """
    構成をもとに内容を追加
    """
    for (class_, title) in layout:
        # 行を足していく
        page.children.add_new(class_, title=title)


def _make_summary_template(page) -> None:
    """
    週次報告用のテンプレートを作成する
    """
    # 週次報告のテンプレートの構成
    summary_layout = [
        (SubheaderBlock, "Summary"),
        (SubsubheaderBlock, "Done"),
        (BulletedListBlock, "-"),
        (SubsubheaderBlock, "Doing"),
        (BulletedListBlock, "-"),
        (SubsubheaderBlock, "TODO"),
        (BulletedListBlock, "-"),
        (SubsubheaderBlock, "Problems"),
        (BulletedListBlock, "-"),
        (TextBlock, ""),
    ]
    # 構成をもとに内容を追加
    _make_content(page, summary_layout)


def _make_daily_template(page) -> None:
    """
    日次報告用のテンプレートを作成する
    """
    # 日次報告のテンプレートの構成
    daily_layout = [
        (SubsubheaderBlock, "Done"),
        (BulletedListBlock, "-"),
        (SubsubheaderBlock, "TODO"),
        (BulletedListBlock, "-"),
        (SubsubheaderBlock, "Problems"),
        (BulletedListBlock, "-"),
    ]
    # 構成をもとに内容を追加
    _make_content(page, daily_layout)


def _make_weekly_from_template(begin_date: datetime.date, title: str = None) -> None:
    """
    1週間分のテンプレートを作成する
    """
    # begin_dateが1月1日なら、end_dateは1月7日
    end_date = begin_date + datetime.timedelta(days=6)
    # "20210101〜20210107"
    week_title = f'{begin_date.strftime("%Y%m%d")}〜{end_date.strftime("%Y%m%d")}'
    # 空ページを作成
    template_page = top_page.children.add_new(
        PageBlock, title=title if title is not None else week_title
    )
    # 週次報告のテンプレートを作成
    _make_summary_template(template_page)
    # コレクションブロックを作成
    cvb = template_page.children.add_new(CollectionViewBlock)
    # コレクションブロックにコレクション本体をアタッチ
    cvb.collection = notion_client.get_collection(
        # テンプレートのプロパティを予め決めたいときは[ここ](https://github.com/jamalex/notion-py/blob/master/notion/smoke_test.py#L240)を参考にする
        notion_client.create_record(
            "collection",
            parent=cvb,
            schema={"title": {"name": "Name", "type": "title"}},
        )
    )
    cvb.title = week_title
    # ビューを追加
    cvb.views.add_new(view_type="list")
    for i in range(7):
        # 日次報告のテンプレートを作成
        row = cvb.collection.add_row()
        row.name = (begin_date + datetime.timedelta(days=i)).strftime("%Y%m%d")
        _make_daily_template(row)


def _make_template() -> None:
    """
    テンプレートを作成する
    """
    _make_weekly_from_template(today, "Template")


if __name__ == "__main__":
    ...
