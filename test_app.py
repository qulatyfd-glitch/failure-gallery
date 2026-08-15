# -*- coding: utf-8 -*-
"""端到端测试：覆盖「非标准答案」所有新功能。
要点：注册后自动登录，所以角色切换必须显式 logout/login。"""
import io
import os
import sys
import tempfile
import base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def make_image(name="test.png"):
    return io.BytesIO(PNG_1PX), name


def register(client, username, password="secret123", with_avatar=False):
    data = {"username": username, "password": password,
            "password2": password, "agree": "on"}
    if with_avatar:
        data["avatar"] = make_image("av.png")
    return client.post("/register", data=data, content_type="multipart/form-data")


def login(client, username, password="secret123"):
    client.post("/logout")
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302


def run():
    tmpdir = tempfile.mkdtemp()
    import app as appmod
    appmod.DB_PATH = os.path.join(tmpdir, "test.db")
    appmod.init_db()
    client = appmod.app.test_client()
    passed = []

    def check(name, cond, extra=""):
        passed.append((name, bool(cond)))
        if not cond:
            print(f"  ✗ {name} {extra}")

    # ===== 用户系统 =====
    r = client.get("/register")
    check("注册页含免责声明", "免责声明".encode() in r.data)

    r = client.post("/register", data={"username": "u1", "password": "secret123",
                                       "password2": "secret123"},
                    content_type="multipart/form-data")
    check("未同意免责声明被拒绝", "免责声明".encode() in r.data)

    r = register(client, "alice", with_avatar=True)
    check("注册后跳转首页", r.status_code == 302)
    check("注册后已登录(首页显示用户名)", "alice".encode() in client.get("/").data)

    r = register(client, "alice")
    check("重复用户名被拒绝", "已经被使用".encode() in r.data)

    # 登出 alice
    client.post("/logout")
    check("退出后首页不显示用户名", "alice".encode() not in client.get("/").data)

    # 登录（错误密码 / 正确密码）
    r = client.post("/login", data={"username": "alice", "password": "wrong"}, follow_redirects=True)
    check("错误密码被拒绝", "用户名或密码不正确".encode() in r.data)
    login(client, "alice")
    r = client.get("/", follow_redirects=True)
    check("正确密码登录成功", "欢迎回来".encode() in r.data)

    # ===== 作品系统：alice 上传带图作品 =====
    r = client.post("/upload", data={
        "title": "算错的数学题",
        "story": "把 7×8 算成 42，衍生出第七维世界观。",
        "category": "wrong_derivation",
        "author": "艾丽丝",
        "image": make_image(),
    }, content_type="multipart/form-data")
    check("上传作品跳详情", r.status_code == 302)
    art_url = r.headers["Location"]
    art_id = int(art_url.rstrip("/").split("/")[-1])
    body = client.get(art_url).data.decode("utf-8")
    check("详情页含标题", "算错的数学题" in body)
    check("详情页含展厅分类", "错误推演" in body)
    check("详情页含署名", "艾丽丝" in body)

    # ===== 草稿（alice 保存，验证自动载入）=====
    r = client.post("/upload", data={"title": "我的草稿", "story": "还没想好", "save_draft": "1"},
                    content_type="multipart/form-data")
    check("保存草稿成功", r.status_code == 302)
    body = client.get("/upload").data.decode("utf-8")
    check("草稿自动载入(标题)", "我的草稿" in body)
    check("草稿自动载入(故事)", "还没想好" in body)

    # ===== 门廊排序 / 搜索 / 分类 / 热度榜 =====
    body = client.get("/?sort=time").data.decode("utf-8")
    check("门廊显示alice作品", "算错的数学题" in body)
    check("门廊显示热度榜区块", "近一周热度榜" in body)
    body = client.get("/?sort=random").data.decode("utf-8")
    check("随机排序页 200", body != "")
    body = client.get("/?category=wrong_derivation").data.decode("utf-8")
    check("展厅筛选(错误推演)", "算错的数学题" in body)
    body = client.get("/?q=第七维").data.decode("utf-8")
    check("搜索命中故事", "算错的数学题" in body)
    body = client.get("/?q=不存在的词xyz").data.decode("utf-8")
    check("搜索无结果提示", "没有找到" in body)

    # ===== bob 注册 + 上传纯文字 =====
    # 当前是 alice 登录，先登出再注册 bob
    client.post("/logout")
    r = register(client, "bob")
    check("bob 注册成功", r.status_code == 302)
    r = client.post("/upload", data={
        "title": "停笔的短篇",
        "story": "写到一半写不下去的宇宙故事，但它自己长出了结局。",
        "category": "stranded",
    }, content_type="multipart/form-data")
    check("bob 纯文字作品上传成功", r.status_code == 302)
    text_url = r.headers["Location"]
    text_id = int(text_url.rstrip("/").split("/")[-1])
    check("纯文字详情页显示'纯文字'", "纯文字作品".encode() in client.get(text_url).data)

    # ===== 点赞 + 通知（bob 赞 alice 的作品）=====
    r = client.post(f"/api/like/{art_id}")
    check("bob 点赞成功", r.get_json()["likes"] == 1)

    # bob 自己看不到 alice 的通知（通知是发给 alice 的）
    body = client.get("/notifications").data.decode("utf-8")
    check("bob 无通知", "赞了你的作品" not in body)

    # bob 取消点赞，再点一次（验证 toggle）
    r = client.post(f"/api/like/{art_id}")
    check("取消点赞 likes=0", r.get_json()["likes"] == 0)
    r = client.post(f"/api/like/{art_id}")
    check("再次点赞 likes=1", r.get_json()["likes"] == 1)

    # bob 评论 + 更多可能性
    r = client.post(f"/api/comment/{art_id}", data={"content": "这个失败很有想象力！"})
    check("发表评论", r.status_code == 200)
    body = client.get(art_url).data.decode("utf-8")
    check("详情页显示评论", "这个失败很有想象力" in body)

    r = client.post(f"/api/interpret/{art_id}", data={"content": "也许 42 才是正解"})
    check("提供更多可能性", r.status_code == 200)
    body = client.get(art_url).data.decode("utf-8")
    check("详情页显示更多可能性", "也许 42 才是正解" in body)

    # ===== 切换回 alice 看通知 =====
    login(client, "alice")
    body = client.get("/notifications").data.decode("utf-8")
    check("alice 收到点赞通知", "赞了你的作品" in body)
    check("alice 收到评论通知", "评论了你的作品" in body)
    check("alice 收到更多可能性通知", "提供了新的可能" in body)
    body = client.get("/").data.decode("utf-8")
    check("门廊无铃铛(通知已移至个人中心)", "🔔" not in body)
    # 通知功能仍有效：个人中心统计里显示未读
    body = client.get("/profile").data.decode("utf-8")
    check("个人中心显示未读通知数", "未读通知" in body)

    client.post("/api/notifications/read")
    body = client.get("/notifications").data.decode("utf-8")
    check("通知页正常", "通知" in body)

    # ===== 编辑（alice 编辑自己的作品）=====
    r = client.post(f"/edit/{art_id}", data={
        "title": "算错的数学题（修订版）",
        "story": "把 7×8 算成 42，衍生出第七维世界观。这是修订。",
        "category": "off_topic",
    })
    check("编辑成功跳详情", r.status_code == 302)
    body = client.get(art_url).data.decode("utf-8")
    check("编辑后标题更新", "修订版" in body)
    check("编辑后分类更新", "偏题" in body)

    # 不能编辑别人的作品
    login(client, "bob")
    r = client.get(f"/edit/{art_id}", follow_redirects=True)
    check("不能编辑他人作品", "只能编辑自己的作品".encode() in r.data)

    # ===== 删除（bob 删自己的纯文字作品）=====
    r = client.post(f"/delete/{text_id}", follow_redirects=True)
    check("删除自己的作品", "作品已删除".encode() in r.data)
    r = client.get(f"/artwork/{text_id}")
    check("删除后详情 404", r.status_code == 404)

    # ===== 举报 + 管理员 =====
    # bob 举报 alice 的作品 1 次
    r = client.post(f"/api/report/{art_id}", data={"reason": "内容有问题"})
    check("bob 举报成功", r.get_json()["ok"] is True)
    body = client.get(art_url).data.decode("utf-8")
    check("举报计数显示 1", "举报 1 次" in body)

    # alice 举报自己的作品 → 应被拒绝
    login(client, "alice")
    r = client.post(f"/api/report/{art_id}", data={"reason": "自检"})
    check("不能举报自己的作品", r.status_code == 400 and "不能举报自己的作品" in r.get_json()["error"])

    # bob 再次举报不行（同一用户只能举报一次）
    login(client, "bob")
    r = client.post(f"/api/report/{art_id}", data={"reason": "再举报"})
    check("同用户重复举报被拒", r.status_code == 400)

    # 第三个用户 carol 举报 → 达阈值下架
    client.post("/logout")
    r = register(client, "carol")
    r = client.post(f"/api/report/{art_id}", data={"reason": "不合适"})
    check("达到阈值自动下架", r.get_json()["hidden"] is True)
    r = client.get(art_url)
    check("下架后详情 404", r.status_code == 404)
    body = client.get("/").data.decode("utf-8")
    check("下架后不在门廊", "算错的数学题" not in body)

    # 管理员登录处理
    client.post("/logout")
    r = client.post("/admin/login", data={"password": "wrong"}, follow_redirects=True)
    check("错误管理员密码拒绝", "管理员密码不正确".encode() in r.data)
    r = client.post("/admin/login", data={"password": "admin123"})
    check("管理员登录成功", r.status_code == 302)
    body = client.get("/admin").data.decode("utf-8")
    check("后台显示下架作品", "算错的数学题" in body)
    r = client.post(f"/admin/action/{art_id}", data={"action": "approve"}, follow_redirects=True)
    check("管理员恢复展示", "已处理".encode() in r.data)
    r = client.get(art_url)
    check("恢复后详情 200", r.status_code == 200)
    body = client.get("/").data.decode("utf-8")
    check("恢复后回到门廊", "算错的数学题" in body)

    # ===== 个人中心 =====
    login(client, "alice")
    body = client.get("/profile").data.decode("utf-8")
    check("个人中心显示用户名", "alice" in body)
    check("个人中心显示我的作品", "算错的数学题" in body)

    # ===== 底部导航 =====
    body = client.get("/").data.decode("utf-8")
    check("首页有底部导航", "bottom-nav" in body)
    check("首页无返回按钮(门廊)", "back-btn" not in body)
    check("底部导航有活动", "活动" in body)
    check("底部导航有想象", "想象" in body)

    # ===== 门廊顶部：无个人中心/通知入口 =====
    check("门廊无个人中心入口", "个人中心" not in body)
    check("门廊无通知入口", "通知" not in body or "查看通知" not in body)
    check("门廊右上角有帮助", "帮助" in body)
    check("门廊右上角有关于我们", "关于我们" in body)

    # ===== 上传页按钮 hover =====
    body = client.get("/upload").data.decode("utf-8")
    check("上传按钮默认文案", "上传我的失败" in body)
    check("上传按钮hover JS(mouseenter)", "mouseenter" in body)
    check("按钮切换文案JS存在", "上传新的成功" in body)

    # ===== 收藏 + 观看历史 =====
    # alice 收藏 bob 的作品(art_id)
    r = client.post(f"/api/favorite/{art_id}")
    check("收藏作品", r.get_json()["favored"] is True)
    r = client.post(f"/api/favorite/{art_id}")
    check("取消收藏", r.get_json()["favored"] is False)
    r = client.post(f"/api/favorite/{art_id}")
    check("再收藏", r.get_json()["favored"] is True)

    # 访问详情页 → 记录观看历史
    client.get(f"/artwork/{art_id}")
    body = client.get("/profile?tab=favorites").data.decode("utf-8")
    check("个人中心-我的收藏标签显示作品", "算错的数学题" in body)
    body = client.get("/profile?tab=history").data.decode("utf-8")
    check("个人中心-观看历史标签显示作品", "算错的数学题" in body)
    check("个人中心有收藏/历史标签页", "我的收藏" in body and "观看历史" in body)

    # 详情页显示收藏按钮
    body = client.get(f"/artwork/{art_id}").data.decode("utf-8")
    check("详情页有收藏按钮", "fav-btn" in body and "已收藏" in body)

    # ===== 活动 + 投票 =====
    # 普通用户不能发布活动
    r = client.post("/activities/new", data={"title": "x", "content": "y"}, follow_redirects=True)
    check("普通用户不能发布活动", "只有管理员可以发布活动" in r.data.decode("utf-8"))

    # 管理员发布活动（投票帖，带选项）
    client.post("/admin/login", data={"password": "admin123"})
    r = client.post("/activities/new", data={"title": "下一期主题征集", "content": "投票选主题",
                                             "is_vote": "on", "tag": "错题世界",
                                             "option_0": "错题宇宙", "option_1": "走神野史",
                                             "option_2": "搁浅创作"}, follow_redirects=True)
    check("管理员发布活动成功", "活动已发布" in r.data.decode("utf-8"))

    # 普通用户投票（单选可改投）
    client.post("/logout")
    login(client, "alice")
    with appmod.get_db() as db:
        act_id = db.execute("SELECT id FROM activities LIMIT 1").fetchone()["id"]
    r = client.post(f"/api/vote/{act_id}", data={"option_index": "0"})
    check("用户投票成功", r.get_json()["voted"] is True and r.get_json()["option_count"] == 1)
    # 改投到选项2
    r = client.post(f"/api/vote/{act_id}", data={"option_index": "1"})
    check("改投成功", r.get_json()["option_count"] == 1 and r.get_json()["option_index"] == 1)
    body = client.get("/activities").data.decode("utf-8")
    check("活动页显示帖子", "下一期主题征集" in body)
    check("活动页显示投票数", "1 票" in body or "(1" in body)
    # 首页显示进行中的活动
    body = client.get("/").data.decode("utf-8")
    check("首页显示进行中活动", "正在进行" in body and "下一期主题征集" in body)

    # 活动详情页：显示选项、可投稿（带活动tag）
    body = client.get(f"/activities/{act_id}").data.decode("utf-8")
    check("活动详情页显示选项", "错题宇宙" in body and "走神野史" in body)
    check("活动详情页有投稿表单", "为活动投稿" in body)

    # 从活动投稿作品（带 tag）
    r = client.post(f"/activities/{act_id}/upload", data={"title": "错题里的漫威",
                                                          "story": "算错的题变成宇宙", "category": "wrong_derivation"},
                    content_type="multipart/form-data")
    check("活动投稿成功", r.status_code == 302)
    body = client.get(f"/activities/{act_id}").data.decode("utf-8")
    check("活动页显示投稿作品", "错题里的漫威" in body)

    # ===== 帮助 / 关于 =====
    body = client.get("/help").data.decode("utf-8")
    check("帮助页有使用指南", "使用指南" in body)
    body = client.get("/about").data.decode("utf-8")
    check("关于我们页有文案", "当下教育困于分数与效率" in body)
    check("关于我们页无小程序", "小程序" not in body)
    check("关于我们页有网站", "网站" in body)
    check("关于我们页有图片", "banner.svg" in body)

    # ===== 想象画布（私有）=====
    r = client.get("/imagine")
    check("想象画布页可访问", r.status_code == 200)
    body = client.get("/imagine").data.decode("utf-8")
    check("想象画布有canvas", "canvas" in body and "save-btn" in body)
    r = client.post("/api/imagine/save", data={"content": '{"strokes":[],"texts":[]}'})
    check("想象画布保存", r.get_json()["ok"] is True)
    r = client.post("/api/imagine/clear")
    check("想象画布清空", r.get_json()["ok"] is True)

    # ===== 登录选项 / 管理员模式 =====
    body = client.get("/account").data.decode("utf-8")
    check("登录选项页有退出登录", "退出登录" in body)
    check("登录选项页有切换账号", "切换账号" in body)
    check("登录选项页有管理员登录", "管理员登录" in body)

    # 普通用户模式底部导航显示"想象"而非"审核"
    body = client.get("/").data.decode("utf-8")
    nav_part = body.split("bottom-nav")[1].split("</nav>")[0] if "bottom-nav" in body else ""
    check("普通模式底部导航有想象链接", "想象" in nav_part)
    check("普通模式底部导航无审核链接", "审核" not in nav_part)

    # 管理员模式：通过 /admin/switch 输入密码
    r = client.post("/admin/switch", data={"password": "admin123"})
    check("管理员模式切换后进门廊", r.status_code == 302 and r.headers["Location"].endswith("/"))
    body = client.get("/").data.decode("utf-8")
    check("管理员模式底部导航有审核", ">审核<" in body or "审核" in body)
    # 精确检查底部导航：含 bn-item 且含审核、不含想象链接
    nav_part = body.split("bottom-nav")[1].split("</nav>")[0] if "bottom-nav" in body else ""
    check("管理员模式底部导航有审核链接", "审核" in nav_part)
    check("管理员模式底部导航无想象链接", "想象" not in nav_part)

    # 审核页
    body = client.get("/review").data.decode("utf-8")
    check("审核页可访问", "待处理" in body or "审核" in body)

    # 退出管理员模式
    client.post("/admin/exit")
    body = client.get("/").data.decode("utf-8")
    check("退出管理员后恢复想象", "想象" in body)

    # 错误管理员密码
    client.post("/logout")
    login(client, "alice")
    r = client.post("/admin/switch", data={"password": "wrong"}, follow_redirects=True)
    check("错误管理员密码被拒", "管理员密码不正确" in r.data.decode("utf-8"))

    # ===== 未登录拦截 =====
    client.post("/logout")
    r = client.get("/upload")
    check("未登录访问上传跳登录", r.status_code == 302 and "/login" in r.headers["Location"])
    r = client.post(f"/api/like/{art_id}")
    check("未登录点赞返回401", r.status_code == 401)

    # ===== 汇总 =====
    total = len(passed)
    ok = sum(1 for _, c in passed if c)
    print(f"\n测试完成：{ok}/{total} 通过")
    for name, cond in passed:
        if not cond:
            print("  失败项:", name)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(run())

