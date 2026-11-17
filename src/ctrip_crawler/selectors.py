"""
页面选择器集中管理。

携程前端改版时，只需要修改本文件即可，无需在爬取逻辑中逐处排查。
每个常量都注明了含义，便于按需调整。

约定：同一字段的多个候选写成「元组」，并且**按优先级从高到低排列**。
批量抽取脚本与 locator 兜底路径都按这个顺序依次尝试，
保证两条路径在页面上存在多个候选元素时解析出的是同一个字段。
"""

# --- 景点搜索结果页 ---
# 全局搜索接口 URL 中包含该关键字，用于拦截 JSON 响应
SEARCH_API_KEYWORD = "getGsOnlineResult"
# 搜索结果页中景点详情链接的选择器（兜底方案：API 未拦截到时使用）
SEARCH_SIGHT_LINK = 'a[href*="/sight/"]'

# --- 景点点评页 ---
# 点评列表容器：等待它出现即说明点评数据已渲染
COMMENT_LIST = ".commentList"
# 单条点评卡片
COMMENT_ITEM = ".commentList > .commentItem"
# 点评正文
COMMENT_DETAIL = ".commentDetail"
# 用户名（直接位于 .userInfo 内，不是 .baseInfo 的子元素）
USER_NAME = ".userName"
# 点评时间与 IP 属地
COMMENT_TIME = ".commentTime"
# 评分。携程自己的类名拼写就是 "scroreInfo"（不是 scoreInfo），
# 其中的 .averageScore 承载了「5分 超棒」这样的文本。
SCORE = ".scroreInfo .averageScore"
# 兼容旧版 / 新版类名（按优先级排列，逐个尝试）
SCORE_FALLBACK = (".scroreInfo", ".scoreInfo .score", ".scoreInfo")

# --- 点评页官方元数据（景区规范名 / 榜单标签 / 综合评分） ---
# 每个字段按优先级从高到低排列，脚本按顺序取第一个非空文本
OFFICIAL_NAME = ("h1", ".titleModule h1", ".title")
OFFICIAL_RANK_TAG = (".rankText", '[class*="rankText"]', '[class*="heatScore"]')
OFFICIAL_SCORE = (
    '[class*="XReviewScoreV2_scoreBox"]',
    ".averageScore",
    '[class*="score_text"]',
)

# --- 分页控件 ---
NEXT_PAGE_BUTTON = ".ant-pagination-next"
# 分页按钮被禁用时的 class 片段，表示已到末页
DISABLED_CLASS = "ant-pagination-disabled"

# --- 目标景点 URL 特征 ---
# 携程景点详情页 URL 形如 https://you.ctrip.com/sight/1/229.html
SIGHT_URL_MARKER = "/sight/"
SIGHT_URL_HOST = "you.ctrip.com"
# 点评页标题中「游玩攻略」之前的部分即为景点名
TITLE_SUFFIX = "游玩攻略"
