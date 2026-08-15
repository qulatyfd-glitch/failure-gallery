# -*- coding: utf-8 -*-
"""
「非标准答案」艺术展 (原「失败美术馆」)
========================================
一个线上艺术展：展示各种"失败"的事物——偏离了原本效果、没有被标准定义的事物。
相信失败之中也能生长出新的想象力与价值。

技术栈：Flask + SQLite + 原生 HTML/CSS/JS
启动：  python app.py
访问：  http://127.0.0.1:5000
"""

import os
import sqlite3
import uuid
from datetime import datetime, timedelta

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, send_from_directory, abort, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash

import img_host

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# 数据目录：可被环境变量覆盖（部署到 PythonAnywhere 时放到用户主目录，保证可写持久）
DATA_DIR = os.environ.get("DB_DIR", BASE_DIR)
UPLOAD_FOLDER = os.path.join(DATA_DIR, "static", "uploads")
AVATAR_FOLDER = os.path.join(DATA_DIR, "static", "avatars")
DB_PATH = os.path.join(DATA_DIR, "instance", "failure.db")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fei-biao-zhun-da-an-dev-secret-2025")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["AVATAR_FOLDER"] = AVATAR_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

CATEGORIES = {
    "off_topic":        ("偏题",     "没有按题目要求完成，却产生了别的方向"),
    "wrong_derivation": ("错误推演", "由计算、逻辑或数据错误生成的新事物"),
    "stranded":         ("搁浅作",   "停在中途、没有结尾、尚未决定用途的作品"),
    "defective":        ("不合格品", "没有通过某项标准，但值得重新观看的结果"),
    "unexpected_use":   ("意外用途", "原本没有实现目标，却产生了新的形式或作用"),
    "uncategorized":    ("无法归类", "不容易被放进任何分类的作品"),
}

REPORT_THRESHOLD = 2


# ---------- 数据库 ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                avatar_filename TEXT,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS artworks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                story TEXT NOT NULL,
                image_filename TEXT,
                is_anonymous INTEGER NOT NULL DEFAULT 0,
                author_name TEXT,
                likes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        cols = {r["name"] for r in db.execute("PRAGMA table_info(artworks)")}
        if "user_id" not in cols:
            db.execute("ALTER TABLE artworks ADD COLUMN user_id INTEGER")
        if "category" not in cols:
            db.execute("ALTER TABLE artworks ADD COLUMN category TEXT DEFAULT 'uncategorized'")
        if "status" not in cols:
            db.execute("ALTER TABLE artworks ADD COLUMN status TEXT DEFAULT 'published'")
        if "updated_at" not in cols:
            db.execute("ALTER TABLE artworks ADD COLUMN updated_at TEXT")
        if "activity_tag" not in cols:
            db.execute("ALTER TABLE artworks ADD COLUMN activity_tag TEXT")

        db.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                artwork_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artwork_id, user_id)
            )
        """)
        lcols = {r["name"] for r in db.execute("PRAGMA table_info(likes)")}
        if "visitor_id" in lcols:
            db.execute("DROP TABLE likes")
            db.execute("""
                CREATE TABLE likes (
                    artwork_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (artwork_id, user_id)
                )
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artwork_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS interpretations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artwork_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                from_user_id INTEGER,
                artwork_id INTEGER NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artwork_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (artwork_id, reporter_id)
            )
        """)
        # 收藏
        db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                artwork_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artwork_id, user_id)
            )
        """)
        # 观看历史
        db.execute("""
            CREATE TABLE IF NOT EXISTS view_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artwork_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                viewed_at TEXT NOT NULL
            )
        """)
        # 活动帖子（仅管理员发布）
        db.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                image_filename TEXT,               -- 活动配图(可选)
                author_id INTEGER,
                is_vote INTEGER NOT NULL DEFAULT 0,   -- 是否为投票帖(下一期主题征集)
                options TEXT,                      -- 投票选项, JSON 数组(仅投票帖)
                tag TEXT,                          -- 活动tag(主题名,如"错题世界")
                created_at TEXT NOT NULL
            )
        """)
        # 旧库迁移：给 activities 补列
        acols = {r["name"] for r in db.execute("PRAGMA table_info(activities)")}
        if "image_filename" not in acols:
            db.execute("ALTER TABLE activities ADD COLUMN image_filename TEXT")
        if "options" not in acols:
            db.execute("ALTER TABLE activities ADD COLUMN options TEXT")
        if "tag" not in acols:
            db.execute("ALTER TABLE activities ADD COLUMN tag TEXT")
        # 活动投票（每个用户每帖一票, 记录投了哪个选项）
        db.execute("""
            CREATE TABLE IF NOT EXISTS activity_votes (
                activity_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                option_index INTEGER NOT NULL DEFAULT -1,  -- 投的选项下标, -1=未指定
                created_at TEXT NOT NULL,
                PRIMARY KEY (activity_id, user_id)
            )
        """)
        # 旧库迁移：给 activity_votes 补 option_index 列
        vcols = {r["name"] for r in db.execute("PRAGMA table_info(activity_votes)")}
        if "option_index" not in vcols:
            db.execute("ALTER TABLE activity_votes ADD COLUMN option_index INTEGER NOT NULL DEFAULT -1")
        # 私有想象画布（仅用户自己可见）
        db.execute("""
            CREATE TABLE IF NOT EXISTS imaginations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,             -- 画布内容(JSON: 图形+文字)
                updated_at TEXT NOT NULL
            )
        """)


# ---------- 工具函数 ----------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_file(file_storage, folder):
    """保存上传文件。若启用外部图床则上传到图床并返回 URL，否则存本地返回文件名。"""
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"

    # 优先尝试外部图床
    try:
        file_storage.seek(0)
        data = file_storage.read()
        url = img_host.upload_to_host(data, new_name)
        if url:
            return url
    except Exception:
        pass  # 图床失败则退回本地

    # 本地存储
    file_storage.seek(0)
    file_storage.save(os.path.join(folder, new_name))
    return new_name


def require_login():
    if not session.get("user_id"):
        session["next"] = request.url
        return redirect(url_for("login"))
    return None


def artwork_display_name(art):
    if art["is_anonymous"]:
        return "匿名"
    if art["author_name"]:
        return art["author_name"]
    return art["username"] or "佚名"


def notify(db, user_id, ntype, from_user_id, artwork_id):
    if from_user_id and from_user_id != user_id:
        db.execute(
            "INSERT INTO notifications (user_id, type, from_user_id, artwork_id, is_read, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, ntype, from_user_id, artwork_id, now_str()),
        )
# ---------- 用户系统 ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        agree = request.form.get("agree") == "on"

        if not username or not password:
            flash("用户名和密码都不能为空。")
        elif len(username) < 2 or len(username) > 20:
            flash("用户名长度需在 2~20 个字符之间。")
        elif len(password) < 6:
            flash("密码至少需要 6 位。")
        elif password != password2:
            flash("两次输入的密码不一致。")
        elif not agree:
            flash("请先阅读并同意免责声明。")
        else:
            with get_db() as db:
                exists = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if exists:
                    flash("这个用户名已经被使用了。")
                else:
                    avatar_filename = None
                    if "avatar" in request.files and request.files["avatar"].filename:
                        av = request.files["avatar"]
                        if not allowed_file(av.filename):
                            flash("头像只支持图片格式。")
                            return render_template("register.html")
                        avatar_filename = save_file(av, app.config["AVATAR_FOLDER"])
                    db.execute(
                        "INSERT INTO users (username, password_hash, avatar_filename, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (username, generate_password_hash(password), avatar_filename, now_str()),
                    )
                    user = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                    session["user_id"] = user["id"]
                    flash("注册成功，欢迎来到「非标准答案」！")
                    return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                flash("登录成功，欢迎回来！")
                nxt = session.pop("next", None)
                return redirect(nxt or url_for("index"))
            flash("用户名或密码不正确。")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("已退出登录。")
    return redirect(url_for("index"))


# ---------- 门廊（首页）----------
@app.route("/")
def index():
    sort = request.args.get("sort", "time")
    q = (request.args.get("q") or "").strip()
    category = request.args.get("category", "")
    page = request.args.get("page", "1")
    page = int(page) if page.isdigit() else 1
    per_page = 12

    where = ["status = 'published'"]
    params = []
    if category in CATEGORIES:
        where.append("category = ?")
        params.append(category)
    if q:
        like_q = f"%{q}%"
        where.append("(a.title LIKE ? OR a.story LIKE ? OR a.author_name LIKE ? OR u.username LIKE ?)")
        params += [like_q, like_q, like_q, like_q]
    where_sql = " AND ".join(where)

    with get_db() as db:
        if sort == "hot":
            order = """
                (SELECT COUNT(*) FROM likes l
                 WHERE l.artwork_id = a.id AND date(l.created_at) = date('now','localtime')) DESC,
                a.created_at DESC, a.id DESC
            """
        elif sort == "random":
            order = "RANDOM()"
        else:
            order = "a.created_at DESC, a.id DESC"

        total = db.execute(
            f"SELECT COUNT(*) FROM artworks a LEFT JOIN users u ON a.user_id=u.id WHERE {where_sql}",
            params,
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT a.*, u.username FROM artworks a
                LEFT JOIN users u ON a.user_id = u.id
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT ? OFFSET ?""",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        artworks = [dict(r) for r in rows]
        for art in artworks:
            art["display_name"] = artwork_display_name(art)

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        hot_rows = db.execute(
            """SELECT a.*, u.username, COUNT(l.user_id) AS week_likes
               FROM artworks a
               LEFT JOIN users u ON a.user_id = u.id
               LEFT JOIN likes l ON l.artwork_id = a.id AND l.created_at >= ?
               WHERE a.status = 'published'
               GROUP BY a.id
               ORDER BY week_likes DESC, a.likes DESC
               LIMIT 5""",
            (week_ago,),
        ).fetchall()
        hot_list = [dict(r) for r in hot_rows]
        for art in hot_list:
            art["display_name"] = artwork_display_name(art)

        total_pages = max(1, (total + per_page - 1) // per_page)

        # 当前进行中的活动（取最近一个）
        active_act = db.execute(
            "SELECT id, title, tag FROM activities ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()

    return render_template("index.html",
                           artworks=artworks, categories=CATEGORIES,
                           sort=sort, q=q, category=category,
                           page=page, total_pages=total_pages,
                           hot_list=hot_list, total=total,
                           active_act=dict(active_act) if active_act else None)
# ---------- 上传 / 编辑 / 删除 / 草稿 ----------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    """上传页。GET 时若有草稿自动载入；POST 可发布或保存草稿。"""
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        story = (request.form.get("story") or "").strip()
        category = request.form.get("category", "uncategorized")
        if category not in CATEGORIES:
            category = "uncategorized"
        is_anonymous = request.form.get("anonymous") == "on"
        author_name = None if is_anonymous else ((request.form.get("author") or "").strip() or user["username"])
        save_draft = request.form.get("save_draft") == "1"

        if not title:
            flash("标题不能为空。")
            return render_template("upload.html", old=request.form, categories=CATEGORIES, editing=None)
        if not story:
            flash("请写下这个「失败」背后的故事。")
            return render_template("upload.html", old=request.form, categories=CATEGORIES, editing=None)

        image_filename = None
        if "image" in request.files and request.files["image"].filename:
            img = request.files["image"]
            if not allowed_file(img.filename):
                flash("图片只支持 png / jpg / jpeg / gif / webp 格式。")
                return render_template("upload.html", old=request.form, categories=CATEGORIES, editing=None)
            image_filename = save_file(img, app.config["UPLOAD_FOLDER"])

        status = "draft" if save_draft else "published"
        now = now_str()

        with get_db() as db:
            draft = db.execute(
                "SELECT * FROM artworks WHERE user_id = ? AND status = 'draft'",
                (user["id"],),
            ).fetchone()
            if draft:
                db.execute(
                    """UPDATE artworks SET title=?, story=?, category=?, is_anonymous=?,
                       author_name=?, image_filename=?, status=?, updated_at=?
                       WHERE id=?""",
                    (title, story, category, 1 if is_anonymous else 0,
                     author_name, image_filename or draft["image_filename"],
                     status, now, draft["id"]),
                )
                art_id = draft["id"]
            else:
                cur = db.execute(
                    """INSERT INTO artworks (user_id, title, story, image_filename, is_anonymous,
                       author_name, likes, category, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                    (user["id"], title, story, image_filename, 1 if is_anonymous else 0,
                     author_name, category, status, now, now),
                )
                art_id = cur.lastrowid

        if save_draft:
            flash("已保存草稿。下次进入上传页会自动载入。")
            return redirect(url_for("upload"))
        return redirect(url_for("detail", artwork_id=art_id))

    draft = None
    with get_db() as db:
        draft = db.execute(
            "SELECT * FROM artworks WHERE user_id = ? AND status = 'draft'",
            (user["id"],),
        ).fetchone()
    old = {}
    if draft:
        old = {"title": draft["title"], "story": draft["story"], "category": draft["category"],
               "anonymous": "on" if draft["is_anonymous"] else "", "author": draft["author_name"],
               "has_image": draft["image_filename"]}
    return render_template("upload.html", old=old, categories=CATEGORIES, editing=None)


@app.route("/artwork/<int:artwork_id>")
def detail(artwork_id):
    """作品详情页：展示作品 + 评论区 + 「更多可能性」子区块 + 举报入口。"""
    with get_db() as db:
        row = db.execute(
            """SELECT a.*, u.username, u.avatar_filename AS author_avatar FROM artworks a
               LEFT JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
            (artwork_id,),
        ).fetchone()
        if row is None or row["status"] != "published":
            abort(404)
        art = dict(row)
        art["display_name"] = artwork_display_name(art)

        comments = db.execute(
            """SELECT c.*, u.username, u.avatar_filename FROM comments c
               LEFT JOIN users u ON c.user_id = u.id
               WHERE c.artwork_id = ? ORDER BY c.created_at ASC""",
            (artwork_id,),
        ).fetchall()
        interpretations = db.execute(
            """SELECT i.*, u.username, u.avatar_filename FROM interpretations i
               LEFT JOIN users u ON i.user_id = u.id
               WHERE i.artwork_id = ? ORDER BY i.created_at ASC""",
            (artwork_id,),
        ).fetchall()
        my_like = None
        my_fav = None
        if session.get("user_id"):
            my_like = db.execute(
                "SELECT 1 FROM likes WHERE artwork_id = ? AND user_id = ?",
                (artwork_id, session["user_id"]),
            ).fetchone()
            my_fav = db.execute(
                "SELECT 1 FROM favorites WHERE artwork_id = ? AND user_id = ?",
                (artwork_id, session["user_id"]),
            ).fetchone()
            # 记录观看历史（保留最近的，去重）
            db.execute("DELETE FROM view_history WHERE artwork_id = ? AND user_id = ?",
                       (artwork_id, session["user_id"]))
            db.execute("INSERT INTO view_history (artwork_id, user_id, viewed_at) VALUES (?, ?, ?)",
                       (artwork_id, session["user_id"], now_str()))
        report_count = db.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE artwork_id = ?", (artwork_id,),
        ).fetchone()["c"]

    return render_template("detail.html", artwork=art, comments=comments,
                           interpretations=interpretations,
                           my_like=bool(my_like), my_fav=bool(my_fav), report_count=report_count,
                           categories=CATEGORIES)


@app.route("/edit/<int:artwork_id>", methods=["GET", "POST"])
def edit(artwork_id):
    """编辑自己的作品。"""
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        art = db.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    if art is None or art["user_id"] != user["id"]:
        flash("只能编辑自己的作品。")
        return redirect(url_for("index"))

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        story = (request.form.get("story") or "").strip()
        category = request.form.get("category", art["category"])
        if category not in CATEGORIES:
            category = art["category"]
        is_anonymous = request.form.get("anonymous") == "on"
        author_name = None if is_anonymous else ((request.form.get("author") or "").strip() or user["username"])
        remove_image = request.form.get("remove_image") == "1"

        if not title or not story:
            flash("标题和故事都不能为空。")
            return render_template("edit.html", art=art, categories=CATEGORIES)

        image_filename = art["image_filename"]
        if remove_image:
            image_filename = None
        if "image" in request.files and request.files["image"].filename:
            img = request.files["image"]
            if not allowed_file(img.filename):
                flash("图片只支持 png / jpg / jpeg / gif / webp 格式。")
                return render_template("edit.html", art=art, categories=CATEGORIES)
            image_filename = save_file(img, app.config["UPLOAD_FOLDER"])

        with get_db() as db:
            db.execute(
                """UPDATE artworks SET title=?, story=?, category=?, is_anonymous=?,
                   author_name=?, image_filename=?, updated_at=? WHERE id=?""",
                (title, story, category, 1 if is_anonymous else 0,
                 author_name, image_filename, now_str(), artwork_id),
            )
        flash("作品已更新。")
        return redirect(url_for("detail", artwork_id=artwork_id))

    return render_template("edit.html", art=art, categories=CATEGORIES)


@app.route("/delete/<int:artwork_id>", methods=["POST"])
def delete(artwork_id):
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        art = db.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
        if art is None or art["user_id"] != user["id"]:
            flash("只能删除自己的作品。")
            return redirect(url_for("index"))
        db.execute("DELETE FROM likes WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM comments WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM interpretations WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM notifications WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM reports WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM favorites WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM view_history WHERE artwork_id = ?", (artwork_id,))
        db.execute("DELETE FROM artworks WHERE id = ?", (artwork_id,))
    flash("作品已删除。")
    return redirect(url_for("profile"))
# ---------- 点赞 / 评论 / 更多可能性 ----------
@app.route("/api/like/<int:artwork_id>", methods=["POST"])
def like(artwork_id):
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()

    with get_db() as db:
        row = db.execute("SELECT id, user_id, likes FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
        if row is None or row["user_id"] is None:
            return jsonify({"error": "作品不存在"}), 404
        existing = db.execute(
            "SELECT 1 FROM likes WHERE artwork_id = ? AND user_id = ?",
            (artwork_id, user["id"]),
        ).fetchone()
        if existing:
            db.execute("DELETE FROM likes WHERE artwork_id = ? AND user_id = ?", (artwork_id, user["id"]))
            db.execute("UPDATE artworks SET likes = likes - 1 WHERE id = ?", (artwork_id,))
            liked = False
        else:
            db.execute(
                "INSERT INTO likes (artwork_id, user_id, created_at) VALUES (?, ?, ?)",
                (artwork_id, user["id"], now_str()),
            )
            db.execute("UPDATE artworks SET likes = likes + 1 WHERE id = ?", (artwork_id,))
            liked = True
            notify(db, row["user_id"], "like", user["id"], artwork_id)
        new_likes = db.execute("SELECT likes FROM artworks WHERE id = ?", (artwork_id,)).fetchone()["likes"]

    return jsonify({"likes": new_likes, "liked": liked})


@app.route("/api/favorite/<int:artwork_id>", methods=["POST"])
def favorite(artwork_id):
    """收藏 / 取消收藏。返回当前收藏状态。"""
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()

    with get_db() as db:
        row = db.execute("SELECT id, user_id FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
        if row is None:
            return jsonify({"error": "作品不存在"}), 404
        existing = db.execute(
            "SELECT 1 FROM favorites WHERE artwork_id = ? AND user_id = ?",
            (artwork_id, user["id"]),
        ).fetchone()
        if existing:
            db.execute("DELETE FROM favorites WHERE artwork_id = ? AND user_id = ?",
                       (artwork_id, user["id"]))
            favored = False
        else:
            db.execute(
                "INSERT INTO favorites (artwork_id, user_id, created_at) VALUES (?, ?, ?)",
                (artwork_id, user["id"], now_str()),
            )
            favored = True

    return jsonify({"favored": favored})


@app.route("/api/comment/<int:artwork_id>", methods=["POST"])
def add_comment(artwork_id):
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"error": "评论内容不能为空"}), 400
    if len(content) > 500:
        return jsonify({"error": "评论最多 500 字"}), 400

    with get_db() as db:
        art = db.execute("SELECT id, user_id FROM artworks WHERE id = ? AND status='published'", (artwork_id,)).fetchone()
        if art is None:
            return jsonify({"error": "作品不存在"}), 404
        db.execute(
            "INSERT INTO comments (artwork_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (artwork_id, user["id"], content, now_str()),
        )
        notify(db, art["user_id"], "comment", user["id"], artwork_id)
    return jsonify({"ok": True})


@app.route("/api/interpret/<int:artwork_id>", methods=["POST"])
def add_interpretation(artwork_id):
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()
    content = (request.form.get("content") or "").strip()
    if not content:
        return jsonify({"error": "内容不能为空"}), 400
    if len(content) > 500:
        return jsonify({"error": "最多 500 字"}), 400

    with get_db() as db:
        art = db.execute("SELECT id, user_id FROM artworks WHERE id = ? AND status='published'", (artwork_id,)).fetchone()
        if art is None:
            return jsonify({"error": "作品不存在"}), 404
        db.execute(
            "INSERT INTO interpretations (artwork_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (artwork_id, user["id"], content, now_str()),
        )
        notify(db, art["user_id"], "interpret", user["id"], artwork_id)
    return jsonify({"ok": True})


# ---------- 通知 ----------
@app.route("/notifications")
def notifications():
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        rows = db.execute(
            """SELECT n.*, a.title AS artwork_title, u.username AS from_username
               FROM notifications n
               LEFT JOIN artworks a ON a.id = n.artwork_id
               LEFT JOIN users u ON u.id = n.from_user_id
               WHERE n.user_id = ? ORDER BY n.created_at DESC LIMIT 100""",
            (user["id"],),
        ).fetchall()
    return render_template("notifications.html", notifications=rows)


@app.route("/api/notifications/read", methods=["POST"])
def mark_notifications_read():
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    with get_db() as db:
        db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (session["user_id"],))
    return jsonify({"ok": True})


# ---------- 个人中心 ----------
@app.route("/profile")
def profile():
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        arts = db.execute(
            "SELECT * FROM artworks WHERE user_id = ? AND status = 'published' ORDER BY created_at DESC, id DESC",
            (user["id"],),
        ).fetchall()
        arts = [dict(r) for r in arts]
        for art in arts:
            art["display_name"] = artwork_display_name(art)

        # 我的收藏
        fav_rows = db.execute(
            """SELECT a.*, u.username FROM favorites f
               JOIN artworks a ON a.id = f.artwork_id
               LEFT JOIN users u ON a.user_id = u.id
               WHERE f.user_id = ? AND a.status = 'published'
               ORDER BY f.created_at DESC""",
            (user["id"],),
        ).fetchall()
        favorites = [dict(r) for r in fav_rows]
        for art in favorites:
            art["display_name"] = artwork_display_name(art)

        # 观看历史
        hist_rows = db.execute(
            """SELECT a.*, u.username FROM view_history v
               JOIN artworks a ON a.id = v.artwork_id
               LEFT JOIN users u ON a.user_id = u.id
               WHERE v.user_id = ? AND a.status = 'published'
               ORDER BY v.viewed_at DESC LIMIT 30""",
            (user["id"],),
        ).fetchall()
        history = [dict(r) for r in hist_rows]
        for art in history:
            art["display_name"] = artwork_display_name(art)

        comments_count = db.execute(
            "SELECT COUNT(*) AS c FROM comments WHERE user_id = ?", (user["id"],),
        ).fetchone()["c"]
        unread = db.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0", (user["id"],),
        ).fetchone()["c"]
    return render_template("profile.html", user=user, artworks=arts,
                           favorites=favorites, history=history,
                           comments_count=comments_count, unread=unread,
                           tab=request.args.get("tab", "works"))


# ---------- 活动 ----------
import json as _json

@app.route("/activities")
def activities():
    """活动界面：帖子列表（含主题投票帖）。"""
    with get_db() as db:
        rows = db.execute(
            """SELECT a.*, u.username AS author_name FROM activities a
               LEFT JOIN users u ON a.author_id = u.id
               ORDER BY a.created_at DESC, a.id DESC""",
        ).fetchall()
        posts = []
        for r in rows:
            post = dict(r)
            # 解析投票选项
            if post.get("options"):
                try:
                    post["option_list"] = _json.loads(post["options"])
                except Exception:
                    post["option_list"] = []
            else:
                post["option_list"] = []
            # 每个选项的票数
            post["option_votes"] = {}
            post["total_votes"] = 0
            post["my_option"] = -1
            votes = db.execute(
                "SELECT option_index, COUNT(*) AS c FROM activity_votes WHERE activity_id = ? GROUP BY option_index",
                (post["id"],),
            ).fetchall()
            for v in votes:
                post["option_votes"][v["option_index"]] = v["c"]
                post["total_votes"] += v["c"]
            if session.get("user_id"):
                my = db.execute(
                    "SELECT option_index FROM activity_votes WHERE activity_id = ? AND user_id = ?",
                    (post["id"], session["user_id"]),
                ).fetchone()
                if my:
                    post["my_option"] = my["option_index"]
            posts.append(post)
    return render_template("activities.html", posts=posts, is_admin=session.get("is_admin"))


@app.route("/activities/<int:activity_id>")
def activity_detail(activity_id):
    """活动帖子详情：可浏览帖子内容，主题帖内可直接上传带 tag 的作品。"""
    with get_db() as db:
        row = db.execute(
            """SELECT a.*, u.username AS author_name FROM activities a
               LEFT JOIN users u ON a.author_id = u.id WHERE a.id = ?""",
            (activity_id,),
        ).fetchone()
        if row is None:
            abort(404)
        post = dict(row)
        if post.get("options"):
            try:
                post["option_list"] = _json.loads(post["options"])
            except Exception:
                post["option_list"] = []
        else:
            post["option_list"] = []
        post["option_votes"] = {}
        post["total_votes"] = 0
        post["my_option"] = -1
        votes = db.execute(
            "SELECT option_index, COUNT(*) AS c FROM activity_votes WHERE activity_id = ? GROUP BY option_index",
            (activity_id,),
        ).fetchall()
        for v in votes:
            post["option_votes"][v["option_index"]] = v["c"]
            post["total_votes"] += v["c"]
        if session.get("user_id"):
            my = db.execute(
                "SELECT option_index FROM activity_votes WHERE activity_id = ? AND user_id = ?",
                (activity_id, session["user_id"]),
            ).fetchone()
            if my:
                post["my_option"] = my["option_index"]
        # 带此活动 tag 的作品
        if post.get("tag"):
            tagged = db.execute(
                """SELECT a.*, u.username FROM artworks a
                   LEFT JOIN users u ON a.user_id = u.id
                   WHERE a.activity_tag = ? AND a.status = 'published'
                   ORDER BY a.created_at DESC""",
                (post["tag"],),
            ).fetchall()
            tagged = [dict(r) for r in tagged]
            for art in tagged:
                art["display_name"] = artwork_display_name(art)
        else:
            tagged = []
    return render_template("activity_detail.html", post=post, tagged=tagged,
                           categories=CATEGORIES, is_admin=session.get("is_admin"))


@app.route("/activities/<int:activity_id>/upload", methods=["POST"])
def activity_upload(activity_id):
    """从主题帖内直接上传作品，自动带上活动 tag。"""
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        act = db.execute("SELECT id, title, tag FROM activities WHERE id = ?", (activity_id,)).fetchone()
    if act is None:
        flash("活动不存在。")
        return redirect(url_for("activities"))

    title = (request.form.get("title") or "").strip()
    story = (request.form.get("story") or "").strip()
    category = request.form.get("category", "uncategorized")
    if category not in CATEGORIES:
        category = "uncategorized"
    is_anonymous = request.form.get("anonymous") == "on"
    author_name = None if is_anonymous else ((request.form.get("author") or "").strip() or user["username"])

    if not title or not story:
        flash("标题和故事都不能为空。")
        return redirect(url_for("activity_detail", activity_id=activity_id))

    image_filename = None
    if "image" in request.files and request.files["image"].filename:
        img = request.files["image"]
        if not allowed_file(img.filename):
            flash("图片只支持 png / jpg / jpeg / gif / webp 格式。")
            return redirect(url_for("activity_detail", activity_id=activity_id))
        image_filename = save_file(img, app.config["UPLOAD_FOLDER"])

    tag = act["tag"] or act["title"]
    with get_db() as db:
        db.execute(
            """INSERT INTO artworks (user_id, title, story, image_filename, is_anonymous,
               author_name, likes, category, status, created_at, updated_at, activity_tag)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'published', ?, ?, ?)""",
            (user["id"], title, story, image_filename, 1 if is_anonymous else 0,
             author_name, category, now_str(), now_str(), tag),
        )
    flash("作品已发布，并带有活动标签「%s」。" % tag)
    return redirect(url_for("activity_detail", activity_id=activity_id))


@app.route("/activities/new", methods=["GET", "POST"])
def activity_new():
    """发布活动帖子（仅管理员），支持配图、投票选项、活动tag。"""
    if not session.get("is_admin"):
        flash("只有管理员可以发布活动。")
        return redirect(url_for("activities"))
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        is_vote = request.form.get("is_vote") == "on"
        tag = (request.form.get("tag") or "").strip()
        if not title or not content:
            flash("标题和内容都不能为空。")
            return render_template("activity_new.html")
        image_filename = None
        if "image" in request.files and request.files["image"].filename:
            img = request.files["image"]
            if not allowed_file(img.filename):
                flash("图片只支持 png / jpg / jpeg / gif / webp 格式。")
                return render_template("activity_new.html")
            image_filename = save_file(img, app.config["UPLOAD_FOLDER"])
        options = None
        if is_vote:
            # 收集管理员填写的投票选项
            opts = []
            for k, v in request.form.items():
                if k.startswith("option_"):
                    val = (v or "").strip()
                    if val:
                        opts.append(val)
            if not opts:
                flash("投票帖需要至少一个选项。")
                return render_template("activity_new.html")
            options = _json.dumps(opts, ensure_ascii=False)
        with get_db() as db:
            db.execute(
                "INSERT INTO activities (title, content, image_filename, author_id, is_vote, options, tag, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (title, content, image_filename, session.get("user_id"),
                 1 if is_vote else 0, options, tag or None, now_str()),
            )
        flash("活动已发布。")
        return redirect(url_for("activities"))
    return render_template("activity_new.html")


@app.route("/activities/<int:activity_id>/delete", methods=["POST"])
def activity_delete(activity_id):
    """删除活动（仅管理员）。"""
    if not session.get("is_admin"):
        flash("只有管理员可以删除活动。")
        return redirect(url_for("activities"))
    with get_db() as db:
        db.execute("DELETE FROM activity_votes WHERE activity_id = ?", (activity_id,))
        db.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    flash("活动已删除。")
    return redirect(url_for("activities"))


@app.route("/api/vote/<int:activity_id>", methods=["POST"])
def vote(activity_id):
    """对主题投票帖的某个选项投票；已投过则改投（单选+可改投）。"""
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()
    option_index = request.form.get("option_index", type=int, default=-1)
    with get_db() as db:
        act = db.execute("SELECT id, is_vote, options FROM activities WHERE id = ?", (activity_id,)).fetchone()
        if act is None:
            return jsonify({"error": "活动不存在"}), 404
        if not act["is_vote"]:
            return jsonify({"error": "这个活动不是投票帖"}), 400
        try:
            opts = _json.loads(act["options"] or "[]")
        except Exception:
            opts = []
        if option_index < 0 or option_index >= len(opts):
            return jsonify({"error": "无效的投票选项"}), 400
        existing = db.execute(
            "SELECT 1 FROM activity_votes WHERE activity_id = ? AND user_id = ?",
            (activity_id, user["id"]),
        ).fetchone()
        if existing:
            # 改投（单选可改）
            db.execute("UPDATE activity_votes SET option_index = ? WHERE activity_id = ? AND user_id = ?",
                       (option_index, activity_id, user["id"]))
        else:
            db.execute("INSERT INTO activity_votes (activity_id, user_id, option_index, created_at) VALUES (?, ?, ?, ?)",
                       (activity_id, user["id"], option_index, now_str()))
        total = db.execute(
            "SELECT COUNT(*) AS c FROM activity_votes WHERE activity_id = ?", (activity_id,),
        ).fetchone()["c"]
        # 该选项票数
        opt_count = db.execute(
            "SELECT COUNT(*) AS c FROM activity_votes WHERE activity_id = ? AND option_index = ?",
            (activity_id, option_index),
        ).fetchone()["c"]
    return jsonify({"voted": True, "total": total, "option_count": opt_count, "option_index": option_index})


# ---------- 想象（私有画布） ----------
@app.route("/imagine")
def imagine():
    """私有画布：随意涂鸦、写文字，仅自己可见。"""
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    user = current_user()
    with get_db() as db:
        row = db.execute("SELECT * FROM imaginations WHERE user_id = ?", (user["id"],)).fetchone()
    content = row["content"] if row else "[]"
    return render_template("imagine.html", content=content)


@app.route("/api/imagine/save", methods=["POST"])
def imagine_save():
    """保存画布内容（JSON）。"""
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()
    content = request.form.get("content", "[]")[:20000]
    with get_db() as db:
        existing = db.execute("SELECT id FROM imaginations WHERE user_id = ?", (user["id"],)).fetchone()
        if existing:
            db.execute("UPDATE imaginations SET content = ?, updated_at = ? WHERE user_id = ?",
                       (content, now_str(), user["id"]))
        else:
            db.execute("INSERT INTO imaginations (user_id, content, updated_at) VALUES (?, ?, ?)",
                       (user["id"], content, now_str()))
    return jsonify({"ok": True})


@app.route("/api/imagine/clear", methods=["POST"])
def imagine_clear():
    """清空画布。"""
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    with get_db() as db:
        db.execute("DELETE FROM imaginations WHERE user_id = ?", (session["user_id"],))
    return jsonify({"ok": True})


# ---------- 登录选项 与 管理员模式 ----------
@app.route("/account")
def account_options():
    """个人中心的「登录选项」：退出登录 / 切换账号 / 管理员登录。"""
    redirect_resp = require_login()
    if redirect_resp:
        return redirect_resp
    return render_template("account.html", is_admin=session.get("is_admin"))


@app.route("/admin/switch", methods=["POST"])
def admin_switch():
    """在「登录选项」页验证管理员密码，进入管理员模式；成功后回到门廊。"""
    if request.form.get("password") == ADMIN_PASSWORD:
        session["is_admin"] = True
        flash("已进入管理员模式。")
        return redirect(url_for("index"))
    flash("管理员密码不正确。")
    return redirect(url_for("account_options"))


@app.route("/admin/exit", methods=["POST"])
def admin_exit():
    """退出管理员模式。"""
    session.pop("is_admin", None)
    flash("已退出管理员模式。")
    return redirect(url_for("index"))


@app.route("/review")
def review():
    """审核界面（管理员模式下的底部导航「审核」）。"""
    if not session.get("is_admin"):
        flash("只有管理员可以进入审核界面。")
        return redirect(url_for("index"))
    with get_db() as db:
        flagged = db.execute(
            """SELECT a.*, u.username FROM artworks a
               LEFT JOIN users u ON a.user_id = u.id
               WHERE a.status = 'flagged' ORDER BY a.id DESC""",
        ).fetchall()
        flagged = [dict(r) for r in flagged]
        for art in flagged:
            art["display_name"] = artwork_display_name(art)
            art["report_count"] = db.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE artwork_id = ?", (art["id"],),
            ).fetchone()["c"]
            art["reports"] = db.execute(
                """SELECT r.*, u.username FROM reports r
                   LEFT JOIN users u ON r.reporter_id = u.id WHERE r.artwork_id = ?""",
                (art["id"],),
            ).fetchall()
    return render_template("admin.html", flagged=flagged)


# ---------- 帮助 与 关于我们 ----------# ---------- 帮助 与 关于我们 ----------# ---------- 帮助 与 关于我们 ----------
@app.route("/help")
def help_page():
    return render_template("help.html")


@app.route("/about")
def about():
    return render_template("about.html")


# ---------- 举报与管理员后台 ----------
@app.route("/api/report/<int:artwork_id>", methods=["POST"])
def report(artwork_id):
    redirect_resp = require_login()
    if redirect_resp:
        return jsonify({"error": "请先登录"}), 401
    user = current_user()
    reason = (request.form.get("reason") or "").strip()[:200]

    with get_db() as db:
        art = db.execute("SELECT id, user_id, status FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
        if art is None:
            return jsonify({"error": "作品不存在"}), 404
        if art["user_id"] == user["id"]:
            return jsonify({"error": "不能举报自己的作品"}), 400
        existing = db.execute(
            "SELECT 1 FROM reports WHERE artwork_id = ? AND reporter_id = ?",
            (artwork_id, user["id"]),
        ).fetchone()
        if existing:
            return jsonify({"error": "你已经举报过这个作品了"}), 400
        db.execute(
            "INSERT INTO reports (artwork_id, reporter_id, reason, created_at) VALUES (?, ?, ?, ?)",
            (artwork_id, user["id"], reason, now_str()),
        )
        cnt = db.execute("SELECT COUNT(*) AS c FROM reports WHERE artwork_id = ?", (artwork_id,)).fetchone()["c"]
        if cnt >= REPORT_THRESHOLD:
            db.execute("UPDATE artworks SET status = 'flagged' WHERE id = ?", (artwork_id,))
            hidden = True
        else:
            hidden = False
    return jsonify({"ok": True, "hidden": hidden})


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("已进入管理员模式。")
            return redirect(url_for("index"))
        flash("管理员密码不正确。")
    return render_template("admin_login.html")


@app.route("/admin")
def admin():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    with get_db() as db:
        flagged = db.execute(
            """SELECT a.*, u.username FROM artworks a
               LEFT JOIN users u ON a.user_id = u.id
               WHERE a.status = 'flagged' ORDER BY a.id DESC""",
        ).fetchall()
        flagged = [dict(r) for r in flagged]
        for art in flagged:
            art["display_name"] = artwork_display_name(art)
            art["report_count"] = db.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE artwork_id = ?", (art["id"],),
            ).fetchone()["c"]
            art["reports"] = db.execute(
                """SELECT r.*, u.username FROM reports r
                   LEFT JOIN users u ON r.reporter_id = u.id WHERE r.artwork_id = ?""",
                (art["id"],),
            ).fetchall()
    return render_template("admin.html", flagged=flagged)


@app.route("/admin/action/<int:artwork_id>", methods=["POST"])
def admin_action(artwork_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    action = request.form.get("action")
    with get_db() as db:
        if action == "approve":
            db.execute("UPDATE artworks SET status = 'published' WHERE id = ?", (artwork_id,))
            db.execute("DELETE FROM reports WHERE artwork_id = ?", (artwork_id,))
        elif action == "delete":
            db.execute("DELETE FROM likes WHERE artwork_id = ?", (artwork_id,))
            db.execute("DELETE FROM comments WHERE artwork_id = ?", (artwork_id,))
            db.execute("DELETE FROM interpretations WHERE artwork_id = ?", (artwork_id,))
            db.execute("DELETE FROM notifications WHERE artwork_id = ?", (artwork_id,))
            db.execute("DELETE FROM reports WHERE artwork_id = ?", (artwork_id,))
            db.execute("DELETE FROM artworks WHERE id = ?", (artwork_id,))
    flash("已处理。")
    return redirect(url_for("review"))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


# ---------- 静态资源与错误处理 ----------
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # 兼容外部图床：文件名是完整 URL 时直接重定向
    if img_host.is_external_url(filename):
        return redirect(filename)
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/avatars/<path:filename>")
def avatar_file(filename):
    if img_host.is_external_url(filename):
        return redirect(filename)
    return send_from_directory(app.config["AVATAR_FOLDER"], filename)


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(e):
    flash("文件太大了，请控制在 8MB 以内。")
    return redirect(url_for("upload"))


@app.context_processor
def inject_globals():
    """让所有模板都能用当前用户 / 分类 / 未读通知数。"""
    user = None
    unread_count = 0
    if session.get("user_id"):
        user = current_user()
        if user:
            with get_db() as db:
                unread_count = db.execute(
                    "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
                    (user["id"],),
                ).fetchone()["c"]
    return dict(current_user=user, categories=CATEGORIES, unread_count=unread_count,
                is_admin=session.get("is_admin", False))


if __name__ == "__main__":
    init_db()
    print("「非标准答案」已启动！")
    print("请打开: http://127.0.0.1:5000")
    print(f"管理员后台: http://127.0.0.1:5000/admin  (默认密码 admin123)")
    app.run(debug=True)


