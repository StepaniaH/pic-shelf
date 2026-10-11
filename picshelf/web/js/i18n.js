// Bilingual (Chinese/English) UI catalog.
// Convention: the Chinese template is the message id; EN maps it to English.
// Usage: t("共 {n} 张", { n: 3 })  →  "共 3 张" (zh) / "3 images" (en)
export const EN = {
  "Pic Shelf — 图片库": "Pic Shelf — Image Library",
  "首页": "Home",
  "收藏": "Favorites",
  "管理": "Admin",
  "搜索：tag / artist:名 / album:名 / rating:>=8 / fav: / ext:png": "Search: tag / artist:name / album:name / rating:>=8 / fav: / ext:png",
  "最新导入": "Newest first",
  "最早导入": "Oldest first",
  "按文件名": "By filename",
  "按我的评分": "By my rating",
  "按平均分": "By average rating",
  "随机": "Random",
  "没有图片": "No images",
  "\n    <div class=\"page-head\">\n      <h1>{v0}</h1><span class=\"count\" id=\"count\">共 {v1} 张</span>\n      <div class=\"toolbar\">\n        {headerExtraHtml}\n        <label class=\"muted\">排序\n          <select id=\"sort-select\">{v2}\n          </select>\n        </label>\n        <button class=\"btn primary\" id=\"slideshow-btn\">幻灯片</button>\n      </div>\n    </div>\n    <div class=\"grid\" id=\"grid\"></div>\n    <div class=\"load-more\"><button class=\"btn\" id=\"more-btn\" hidden>加载更多</button></div>": "\n    <div class=\"page-head\">\n      <h1>{v0}</h1><span class=\"count\" id=\"count\">{v1} images</span>\n      <div class=\"toolbar\">\n        {headerExtraHtml}\n        <label class=\"muted\">Sort\n          <select id=\"sort-select\">{v2}\n          </select>\n        </label>\n        <button class=\"btn primary\" id=\"slideshow-btn\">Slideshow</button>\n      </div>\n    </div>\n    <div class=\"grid\" id=\"grid\"></div>\n    <div class=\"load-more\"><button class=\"btn\" id=\"more-btn\" hidden>Load more</button></div>",
  "共 {v0} 张": "{v0} images",
  "\n    <div class=\"login-wrap\">\n      <h1>登录 Pic Shelf</h1>\n      <form id=\"login-form\">\n        <input type=\"text\" id=\"login-username\" placeholder=\"用户名\" autocomplete=\"username\" autofocus>\n        <input type=\"password\" id=\"login-password\" placeholder=\"密码\" autocomplete=\"current-password\">\n        <button class=\"btn primary\" type=\"submit\" style=\"width:100%\">登录</button>\n      </form>\n      <p class=\"hint\">没有账号？现有用户可在「管理 → 用户」中创建，或在服务器上运行 <code>picshelf users add 名字</code>。</p>\n    </div>": "\n    <div class=\"login-wrap\">\n      <h1>Sign in to Pic Shelf</h1>\n      <form id=\"login-form\">\n        <input type=\"text\" id=\"login-username\" placeholder=\"Username\" autocomplete=\"username\" autofocus>\n        <input type=\"password\" id=\"login-password\" placeholder=\"Password\" autocomplete=\"current-password\">\n        <button class=\"btn primary\" type=\"submit\" style=\"width:100%\">Sign in</button>\n      </form>\n      <p class=\"hint\">No account? Existing users can create one under Admin → Users, or run <code>picshelf users add &lt;name&gt;</code> on the server.</p>\n    </div>",
  "登录失败": "Sign-in failed",
  "图片": "Images",
  "库": "Libraries",
  "专辑": "Albums",
  "艺术家": "Artists",
  "标签": "Tags",
  "用户": "Users",
  "\n    <div class=\"page-head\"><h1>首页</h1></div>\n    <div class=\"tiles\">{v0}\n    </div>\n    <h2>库</h2>\n    <div class=\"lib-cards\">{v1}\n    </div>": "\n    <div class=\"page-head\"><h1>Home</h1></div>\n    <div class=\"tiles\">{v0}\n    </div>\n    <h2>Libraries</h2>\n    <div class=\"lib-cards\">{v1}\n    </div>",
  "扫描中…": "Scanning…",
  "扫描完成：新增 {v0}，更新 {v1}，移动 {v2}，缺失 {v3}": "Scan done: added {v0}, updated {v1}, moved {v2}, missing {v3}",
  "扫描更新": "Rescan",
  "（无库）": "(no libraries)",
  "\n    <div class=\"page-head\"><h1>专辑</h1><span class=\"count\">共 {v0} 个</span></div>\n    {v1}": "\n    <div class=\"page-head\"><h1>Albums</h1><span class=\"count\">{v0} total</span></div>\n    {v1}",
  "专辑：{v0}": "Album: {v0}",
  "<a class=\"btn small\" href=\"#/albums\">← 全部专辑</a>": "<a class=\"btn small\" href=\"#/albums\">← All albums</a>",
  "\n    <div class=\"page-head\"><h1>艺术家</h1><span class=\"count\">共 {v0} 位</span></div>\n    <div class=\"chips\">{v1}\n    </div>": "\n    <div class=\"page-head\"><h1>Artists</h1><span class=\"count\">{v0} artists</span></div>\n    <div class=\"chips\">{v1}\n    </div>",
  "艺术家：{v0}": "Artist: {v0}",
  "<span class=\"pill\">别名：{v0}</span>": "<span class=\"pill\">Aliases: {v0}</span>",
  "<a class=\"btn small\" href=\"#/artists\">← 全部艺术家</a>": "<a class=\"btn small\" href=\"#/artists\">← All artists</a>",
  "\n    <div class=\"page-head\"><h1>标签</h1><span class=\"count\">共 {v0} 个</span></div>\n    <div class=\"chips\">{v1}\n    </div>": "\n    <div class=\"page-head\"><h1>Tags</h1><span class=\"count\">{v0} tags</span></div>\n    <div class=\"chips\">{v1}\n    </div>",
  "标签：{name}": "Tag: {name}",
  "<a class=\"btn small\" href=\"#/tags\">← 全部标签</a>": "<a class=\"btn small\" href=\"#/tags\">← All tags</a>",
  "我的收藏": "My favorites",
  "搜索：{q}": "Search: {q}",
  "搜索（全部）": "Search (all)",
  "<a class=\"btn small\" href=\"#/home\">← 首页</a>": "<a class=\"btn small\" href=\"#/home\">← Home</a>",
  "库：{v0}": "Library: {v0}",
  "<button class=\"btn small\" id=\"lib-scan\">扫描更新</button>": "<button class=\"btn small\" id=\"lib-scan\">Rescan</button>",
  "<span class=\"muted\">上次扫描：{v0}</span>": "<span class=\"muted\">Last scan: {v0}</span>",
  "<a class=\"btn small\" href=\"#/albums?library_id={libraryId}\">专辑</a>{scanBtn}{last}": "<a class=\"btn small\" href=\"#/albums?library_id={libraryId}\">Albums</a>{scanBtn}{last}",
  "标签：{v0}": "Tag: {v0}",
  "全部图片": "All images",
  "适应": "Fit",
  "填满": "Fill",
  "原图": "Original",
  "<p class=\"muted\">信息加载失败：{v0}</p>": "<p class=\"muted\">Failed to load info: {v0}</p>",
  "共 {v0}": "{v0} total",
  "已加载 {v0} / 共 {v1}": "Loaded {v0} / {v1}",
  "\n      <div class=\"viewer-top\">\n        <span class=\"title\">{v0}</span>\n        <span class=\"muted meta-count\">{v1} / {v2}（{loaded}）</span>\n        <div class=\"viewer-actions\">\n          <button class=\"btn small\" id=\"v-slideshow\">幻灯片</button>\n          <button class=\"btn small\" id=\"v-zoom\" title=\"切换显示尺寸 (z)\">尺寸：{v3}</button>\n          {v4}\n          <button class=\"btn small\" id=\"v-panel\" title=\"信息面板 (i)\">信息</button>\n          <a class=\"btn small\" href=\"{v5}?download=1\">下载原图</a>\n          <button class=\"btn small\" id=\"v-close\" title=\"关闭 (Esc)\">关闭 ✕</button>\n        </div>\n      </div>\n      <div class=\"viewer-body\">\n        <div class=\"viewer-stage size-{zoom}\">\n          <img src=\"{v6}\" alt=\"{v7}\">\n          <button class=\"nav-btn prev\" title=\"上一张 (←)\">‹</button>\n          <button class=\"nav-btn next\" title=\"下一张 (→)\">›</button>\n        </div>\n        <aside class=\"viewer-panel\" {v8}>{v9}</aside>\n      </div>": "\n      <div class=\"viewer-top\">\n        <span class=\"title\">{v0}</span>\n        <span class=\"muted meta-count\">{v1} / {v2} ({loaded})</span>\n        <div class=\"viewer-actions\">\n          <button class=\"btn small\" id=\"v-slideshow\">Slideshow</button>\n          <button class=\"btn small\" id=\"v-zoom\" title=\"Toggle display size (z)\">Size: {v3}</button>\n          {v4}\n          <button class=\"btn small\" id=\"v-panel\" title=\"Info panel (i)\">Info</button>\n          <a class=\"btn small\" href=\"{v5}?download=1\">Download original</a>\n          <button class=\"btn small\" id=\"v-close\" title=\"Close (Esc)\">Close ✕</button>\n        </div>\n      </div>\n      <div class=\"viewer-body\">\n        <div class=\"viewer-stage size-{zoom}\">\n          <img src=\"{v6}\" alt=\"{v7}\">\n          <button class=\"nav-btn prev\" title=\"Previous (←)\">‹</button>\n          <button class=\"nav-btn next\" title=\"Next (→)\">›</button>\n        </div>\n        <aside class=\"viewer-panel\" {v8}>{v9}</aside>\n      </div>",
  "<p class=\"muted\">加载中…</p>": "<p class=\"muted\">Loading…</p>",
  "\n      <section>\n        <h3>评分 <span class=\"muted\">（平均 {v0} · {v1} 人）</span></h3>\n        {v2}\n      </section>\n      <section>\n        <h3>标签</h3>\n        <div>{v3}\n        </div>\n        {v4}\n      </section>\n      <section>\n        <h3>艺术家</h3>\n        <div>{v5}\n        </div>\n        {v6}\n      </section>\n      <section>\n        <h3>专辑</h3>\n        <div>{v7}</div>\n        {v8}\n      </section>\n      <section>\n        <h3>评论（{v9}）</h3>\n        <div id=\"comment-list\">{v10}</div>\n        {v11}\n      </section>\n      <section>\n        <h3>笔记（{v12}）</h3>\n        {v13}\n        {v14}\n      </section>\n      <section>\n        <h3>信息</h3>\n        <div class=\"kv\">\n          <div>文件名：<b>{v15}</b></div>\n          <div>尺寸：<b>{v16}×{v17}</b> · 大小：<b>{v18} KB</b> · 格式：<b>{v19}</b></div>\n          <div>库：<b>{v20}</b></div>\n          <div>路径：<b>{v21}</b></div>\n          <div>导入时间：<b>{v22}</b></div>\n          <div>指纹：<b title=\"{v23}\">{v24}…</b> / pHash <b>{v25}</b></div>\n        </div>\n      </section>": "\n      <section>\n        <h3>Rating <span class=\"muted\">(avg {v0} · {v1} users)</span></h3>\n        {v2}\n      </section>\n      <section>\n        <h3>Tags</h3>\n        <div>{v3}\n        </div>\n        {v4}\n      </section>\n      <section>\n        <h3>Artists</h3>\n        <div>{v5}\n        </div>\n        {v6}\n      </section>\n      <section>\n        <h3>Albums</h3>\n        <div>{v7}</div>\n        {v8}\n      </section>\n      <section>\n        <h3>Comments ({v9})</h3>\n        <div id=\"comment-list\">{v10}</div>\n        {v11}\n      </section>\n      <section>\n        <h3>Notes ({v12})</h3>\n        {v13}\n        {v14}\n      </section>\n      <section>\n        <h3>Info</h3>\n        <div class=\"kv\">\n          <div>Filename: <b>{v15}</b></div>\n          <div>Dimensions: <b>{v16}×{v17}</b> · size: <b>{v18} KB</b> · format: <b>{v19}</b></div>\n          <div>Library: <b>{v20}</b></div>\n          <div>Path: <b>{v21}</b></div>\n          <div>Imported: <b>{v22}</b></div>\n          <div>Digest: <b title=\"{v23}\">{v24}…</b> / pHash <b>{v25}</b></div>\n        </div>\n      </section>",
  "已收藏": "Favorited",
  "已取消收藏": "Unfavorited",
  "已清除评分": "Rating cleared",
  "评分 → {v}": "Rating → {v}",
  "已添加标签：{name}": "Tag added: {name}",
  "已移除标签": "Tag removed",
  "已关联艺术家：{name}": "Artist linked: {name}",
  "已取消关联": "Artist unlinked",
  "<option value=\"\">加入手工专辑…</option>": "<option value=\"\">Add to manual album…</option>",
  "已加入专辑": "Added to album",
  "评论已发表": "Comment posted",
  "评论已删除": "Comment deleted",
  "笔记已添加": "Note added",
  "顺序": "Sequential",
  "乱序": "Shuffled",
  "当前列表": "Current list",
  "幻灯片设置": "Slideshow settings",
  "\n      <div class=\"form-row\"><label class=\"muted\">来源</label><span>{v0}</span></div>\n      <div class=\"form-row\">\n        <label>间隔 <select id=\"ss-interval\">{v1}\n        </select> 秒</label>\n        <label>顺序 <select id=\"ss-order\">{v2}\n        </select></label>\n      </div>\n      <div class=\"form-row\">\n        <label>数量 <input id=\"ss-count\" type=\"number\" min=\"0\" value=\"0\" style=\"width:90px\"> <span class=\"muted\">0 = 全部</span></label>\n      </div>\n      <div class=\"form-row checkbox-row\">\n        <label><input type=\"checkbox\" id=\"ss-loop\" {v3}> 循环（轮播）</label>\n        <label><input type=\"checkbox\" id=\"ss-captions\" {v4}> 显示字幕（文件名/标签）</label>\n      </div>\n      <div class=\"form-row\">\n        <label>尺寸 <select id=\"ss-size\">{v5}\n        </select></label>\n      </div>\n      <p class=\"hint\">播放中：空格 暂停/继续 · ←/→ 上一张/下一张 · Esc 退出</p>\n      <div class=\"modal-actions\">\n        <button class=\"btn primary\" id=\"ss-start\">开始播放</button>\n        <button class=\"btn\" data-close>取消</button>\n      </div>": "\n      <div class=\"form-row\"><label class=\"muted\">Source</label><span>{v0}</span></div>\n      <div class=\"form-row\">\n        <label>Interval <select id=\"ss-interval\">{v1}\n        </select> sec</label>\n        <label>Order <select id=\"ss-order\">{v2}\n        </select></label>\n      </div>\n      <div class=\"form-row\">\n        <label>Count <input id=\"ss-count\" type=\"number\" min=\"0\" value=\"0\" style=\"width:90px\"> <span class=\"muted\">0 = all</span></label>\n      </div>\n      <div class=\"form-row checkbox-row\">\n        <label><input type=\"checkbox\" id=\"ss-loop\" {v3}> Loop (carousel)</label>\n        <label><input type=\"checkbox\" id=\"ss-captions\" {v4}> Show captions (filename/tags)</label>\n      </div>\n      <div class=\"form-row\">\n        <label>Size <select id=\"ss-size\">{v5}\n        </select></label>\n      </div>\n      <p class=\"hint\">While playing: space pause/resume · ←/→ prev/next · Esc exit</p>\n      <div class=\"modal-actions\">\n        <button class=\"btn primary\" id=\"ss-start\">Start</button>\n        <button class=\"btn\" data-close>Cancel</button>\n      </div>",
  "播放失败": "Playback failed",
  "来源为空，没有可播放的图片": "Empty source — nothing to play",
  "\n    <div class=\"slideshow-stage size-{v0}\">\n      <img id=\"ss-img\" alt=\"\">\n      <div class=\"slideshow-controls\">\n        <button class=\"btn small\" id=\"ss-prev\">‹ 上一张</button>\n        <button class=\"btn small\" id=\"ss-pause\">⏸ 暂停</button>\n        <button class=\"btn small\" id=\"ss-next\">下一张 ›</button>\n        <button class=\"btn small\" id=\"ss-settings\">设置</button>\n        <button class=\"btn small\" id=\"ss-exit\">退出 (Esc)</button>\n      </div>\n    </div>\n    {v1}": "\n    <div class=\"slideshow-stage size-{v0}\">\n      <img id=\"ss-img\" alt=\"\">\n      <div class=\"slideshow-controls\">\n        <button class=\"btn small\" id=\"ss-prev\">‹ Prev</button>\n        <button class=\"btn small\" id=\"ss-pause\">⏸ Pause</button>\n        <button class=\"btn small\" id=\"ss-next\">Next ›</button>\n        <button class=\"btn small\" id=\"ss-settings\">Settings</button>\n        <button class=\"btn small\" id=\"ss-exit\">Exit (Esc)</button>\n      </div>\n    </div>\n    {v1}",
  "播放完成": "Playback finished",
  "▶ 继续": "▶ Resume",
  "⏸ 暂停": "⏸ Pause",
  "查看": "View",
  "评分": "Rate",
  "评论": "Comment",
  "笔记": "Note",
  "标签/专辑编辑": "Tag/album editing",
  "库管理": "Library management",
  "管理权限": "Admin",
  "<p class=\"empty\">需要管理员权限。</p>": "<p class=\"empty\">Admin privileges required.</p>",
  "\n    <div class=\"page-head\"><h1>管理</h1></div>\n    <div class=\"admin-tabs\">\n      <button class=\"btn\" data-tab=\"users\">用户</button>\n      <button class=\"btn\" data-tab=\"groups\">用户组</button>\n      <button class=\"btn\" data-tab=\"import\">导入</button>\n      <button class=\"btn\" data-tab=\"libraries\">库</button>\n    </div>\n    <div id=\"admin-body\"></div>": "\n    <div class=\"page-head\"><h1>Admin</h1></div>\n    <div class=\"admin-tabs\">\n      <button class=\"btn\" data-tab=\"users\">Users</button>\n      <button class=\"btn\" data-tab=\"groups\">Groups</button>\n      <button class=\"btn\" data-tab=\"import\">Import</button>\n      <button class=\"btn\" data-tab=\"libraries\">Libraries</button>\n    </div>\n    <div id=\"admin-body\"></div>",
  "\n    <h2>用户</h2>\n    <div class=\"form-row\">\n      <input id=\"nu-name\" placeholder=\"用户名\" style=\"width:150px\">\n      <input id=\"nu-pass\" type=\"password\" placeholder=\"密码（≥6 位）\" style=\"width:170px\">\n      <label class=\"muted\"><input type=\"checkbox\" id=\"nu-admin\"> 管理员</label>\n      <button class=\"btn primary\" id=\"nu-create\">创建用户</button>\n    </div>\n    <div class=\"table-wrap\">\n    <table class=\"data\"><thead><tr><th>ID</th><th>用户名</th><th>状态</th><th>用户组</th><th>操作</th></tr></thead>\n    <tbody>{v0}\n    </tbody></table>\n    </div>": "\n    <h2>Users</h2>\n    <div class=\"form-row\">\n      <input id=\"nu-name\" placeholder=\"Username\" style=\"width:150px\">\n      <input id=\"nu-pass\" type=\"password\" placeholder=\"Password (≥6 chars)\" style=\"width:170px\">\n      <label class=\"muted\"><input type=\"checkbox\" id=\"nu-admin\"> Admin</label>\n      <button class=\"btn primary\" id=\"nu-create\">Create user</button>\n    </div>\n    <div class=\"table-wrap\">\n    <table class=\"data\"><thead><tr><th>ID</th><th>Username</th><th>Status</th><th>Groups</th><th>Actions</th></tr></thead>\n    <tbody>{v0}\n    </tbody></table>\n    </div>",
  "已创建用户：{username}": "User created: {username}",
  "为 {v0} 设置新密码（≥6 位）": "New password for {v0} (≥6 chars)",
  "密码已重置": "Password reset",
  "\n        <div class=\"checkbox-row\">{v0}</div>\n        <div class=\"modal-actions\"><button class=\"btn primary\" id=\"ug-save\">保存</button><button class=\"btn\" data-close>取消</button></div>": "\n        <div class=\"checkbox-row\">{v0}</div>\n        <div class=\"modal-actions\"><button class=\"btn primary\" id=\"ug-save\">Save</button><button class=\"btn\" data-close>Cancel</button></div>",
  "用户组：{v0}": "Groups: {v0}",
  "用户组已更新": "Groups updated",
  "\n    <h2>用户组</h2>\n    <div class=\"form-row\">\n      <input id=\"ng-name\" placeholder=\"组名\" style=\"width:150px\">\n      <input id=\"ng-desc\" placeholder=\"描述（可选）\" style=\"width:230px\">\n      <button class=\"btn primary\" id=\"ng-create\">创建组</button>\n    </div>\n    {v0}\n    <datalist id=\"user-dl\">{v1}</datalist>": "\n    <h2>Groups</h2>\n    <div class=\"form-row\">\n      <input id=\"ng-name\" placeholder=\"Group name\" style=\"width:150px\">\n      <input id=\"ng-desc\" placeholder=\"Description (optional)\" style=\"width:230px\">\n      <button class=\"btn primary\" id=\"ng-create\">Create group</button>\n    </div>\n    {v0}\n    <datalist id=\"user-dl\">{v1}</datalist>",
  "已创建组：{name}": "Group created: {name}",
  "找不到该用户": "User not found",
  "已保存": "Saved",
  "删除组": "Delete group",
  "确定删除组「{v0}」？成员关系会一并移除。": "Delete group \"{v0}\"? Memberships will be removed too.",
  "已删除": "Deleted",
  "\n    <h2>库</h2>\n    <div class=\"form-row\">\n      <input id=\"nl-name\" placeholder=\"库名\" style=\"width:140px\">\n      <input id=\"nl-path\" placeholder=\"/绝对路径/到/图片目录\" style=\"width:320px\">\n      <input id=\"nl-desc\" placeholder=\"描述（可选）\">\n      <button class=\"btn primary\" id=\"nl-create\">注册库</button>\n    </div>\n    {v0}": "\n    <h2>Libraries</h2>\n    <div class=\"form-row\">\n      <input id=\"nl-name\" placeholder=\"Library name\" style=\"width:140px\">\n      <input id=\"nl-path\" placeholder=\"/absolute/path/to/images\" style=\"width:320px\">\n      <input id=\"nl-desc\" placeholder=\"Description (optional)\">\n      <button class=\"btn primary\" id=\"nl-create\">Register library</button>\n    </div>\n    {v0}",
  "已注册库：{name}": "Library registered: {name}",
  "\n        <label class=\"grant-item\" style=\"display:flex;justify-content:space-between;align-items:center;gap:8px\">\n          <span>{v0}</span>\n          <select data-grant-gid=\"{v1}\">\n            <option value=\"\" {v2}>无</option>\n            <option value=\"view\" {v3}>查看</option>\n            <option value=\"edit\" {v4}>编辑</option>\n            <option value=\"manage\" {v5}>管理</option>\n          </select>\n        </label>": "\n        <label class=\"grant-item\" style=\"display:flex;justify-content:space-between;align-items:center;gap:8px\">\n          <span>{v0}</span>\n          <select data-grant-gid=\"{v1}\">\n            <option value=\"\" {v2}>None</option>\n            <option value=\"view\" {v3}>View</option>\n            <option value=\"edit\" {v4}>Edit</option>\n            <option value=\"manage\" {v5}>Manage</option>\n          </select>\n        </label>",
  "授权已更新": "Grants updated",
  "(无权限查看授权)": "(no permission to view grants)",
  "\n    <h2>托管导入</h2>\n    <p class=\"hint\">把外部目录的图片导入到托管的库。默认「硬链接」：原文件不动、库内出现规范化命名、同盘零额外空间（跨盘自动降级为复制）。先「预演」，确认无误后再「执行」。</p>\n    <div class=\"form-row\">\n      <input id=\"imp-src\" placeholder=\"源目录（绝对路径）\" style=\"min-width:300px;flex:1\">\n      <select id=\"imp-lib\">{v0}</select>\n      <select id=\"imp-mode\">\n        <option value=\"hardlink\">硬链接（推荐 · 原文件不动）</option>\n        <option value=\"copy\">复制（原库留档）</option>\n        <option value=\"move\">移动（整理 · 原位置不保留）</option>\n      </select>\n      <label class=\"muted\"><input type=\"checkbox\" id=\"imp-rename\" checked> 文件名规范化</label>\n    </div>\n    <div class=\"form-row\">\n      <button class=\"btn primary\" id=\"imp-plan\">预演</button>\n      <button class=\"btn danger\" id=\"imp-exec\" disabled>执行</button>\n      {v1}\n    </div>\n    <div id=\"imp-out\" class=\"muted\">尚未预演。</div>": "\n    <h2>Managed import</h2>\n    <p class=\"hint\">Import images from an external directory into a managed library. Default \"hardlink\": originals stay untouched, normalized names inside the library, zero extra space on the same disk (falls back to copy across disks). Do a \"Dry run\" first, then \"Execute\".</p>\n    <div class=\"form-row\">\n      <input id=\"imp-src\" placeholder=\"Source directory (absolute path)\" style=\"min-width:300px;flex:1\">\n      <select id=\"imp-lib\">{v0}</select>\n      <select id=\"imp-mode\">\n        <option value=\"hardlink\">Hardlink (recommended · originals untouched)</option>\n        <option value=\"copy\">Copy (keep the source)</option>\n        <option value=\"move\">Move (tidy · originals not kept)</option>\n      </select>\n      <label class=\"muted\"><input type=\"checkbox\" id=\"imp-rename\" checked> Normalize filenames</label>\n    </div>\n    <div class=\"form-row\">\n      <button class=\"btn primary\" id=\"imp-plan\">Dry run</button>\n      <button class=\"btn danger\" id=\"imp-exec\" disabled>Execute</button>\n      {v1}\n    </div>\n    <div id=\"imp-out\" class=\"muted\">Not planned yet.</div>",
  "请填写源目录": "Enter a source directory",
  "预演中…": "Planning…",
  "预演失败：": "Dry run failed: ",
  "预演": "Dry run",
  "硬链接": "Hardlink",
  "复制": "Copy",
  "移动": "Move",
  "注意：移动模式下原位置的文件将被移走，不再保留。": "Note: in move mode files will be removed from their original location.",
  "确认执行导入": "Confirm import",
  "将处理 {newN} 个文件（模式：{modeText}）。{warn} 将写入目标库目录。": "About to process {newN} files (mode: {modeText}). {warn} Writing into the target library directory.",
  "执行中…": "Executing…",
  "执行完成：硬链 {v0}，复制 {v1}，移动 {v2}，跳过 {v3}，错误 {v4}": "Done: hardlinked {v0}, copied {v1}, moved {v2}, skipped {v3}, errors {v4}",
  "执行": "Execute",
  "硬链": "Hardlink",
  "跳过": "Skip",
  "错误": "Error",
  "\n    <p>共 <b>{v0}</b> 个文件：新导入 <b>{v1}</b>，跳过 <b>{v2}</b>（重复/已存在），错误 <b>{v3}</b>。\n    {v4}</p>\n    <div class=\"table-wrap\"><table class=\"data imp-table\"><thead><tr><th>动作</th><th>源</th><th>目标</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></div>\n    <p class=\"hint\">以上为预演结果，未改动任何文件。确认后点「执行」。</p>": "\n    <p><b>{v0}</b> files: new <b>{v1}</b>, skipped <b>{v2}</b> (duplicate/existing), errors <b>{v3}</b>.\n    {v4}</p>\n    <div class=\"table-wrap\"><table class=\"data imp-table\"><thead><tr><th>Action</th><th>Source</th><th>Target</th><th>Note</th></tr></thead><tbody>{rows}</tbody></table></div>\n    <p class=\"hint\">Dry-run result — nothing changed. Click \"Execute\" to proceed.</p>",
  "<p>执行完成：硬链 <b>{v0}</b>，复制 <b>{v1}</b>，移动 <b>{v2}</b>，跳过 <b>{v3}</b>，错误 <b>{v4}</b>。\n  {v5}</p>\n  {v6}": "<p>Done: hardlinked <b>{v0}</b>, copied <b>{v1}</b>, moved <b>{v2}</b>, skipped <b>{v3}</b>, errors <b>{v4}</b>.\n  {v5}</p>\n  {v6}",
  "\n    <div class=\"modal\" role=\"dialog\" aria-modal=\"true\" aria-label=\"{v0}\">\n      <div class=\"modal-head\"><h2>{v1}</h2><button class=\"icon-btn\" data-close aria-label=\"关闭\">✕</button></div>\n      <div class=\"modal-body\"></div>\n    </div>": "\n    <div class=\"modal\" role=\"dialog\" aria-modal=\"true\" aria-label=\"{v0}\">\n      <div class=\"modal-head\"><h2>{v1}</h2><button class=\"icon-btn\" data-close aria-label=\"Close\">✕</button></div>\n      <div class=\"modal-body\"></div>\n    </div>",
  "<form class=\"prompt-form\"><input type=\"{type}\" value=\"{v0}\" placeholder=\"{v1}\" autocomplete=\"off\"><div class=\"modal-actions\"><button type=\"submit\" class=\"btn primary\">确定</button><button type=\"button\" class=\"btn\" data-cancel>取消</button></div></form>": "<form class=\"prompt-form\"><input type=\"{type}\" value=\"{v0}\" placeholder=\"{v1}\" autocomplete=\"off\"><div class=\"modal-actions\"><button type=\"submit\" class=\"btn primary\">OK</button><button type=\"button\" class=\"btn\" data-cancel>Cancel</button></div></form>",
  "<p>{v0}</p><div class=\"modal-actions\"><button class=\"btn primary\" data-yes>确定</button><button class=\"btn\" data-no>取消</button></div>": "<p>{v0}</p><div class=\"modal-actions\"><button class=\"btn primary\" data-yes>OK</button><button class=\"btn\" data-no>Cancel</button></div>",
  "<span class=\"username\">{v0}</span>\n      <button class=\"btn small\" id=\"logout-btn\">退出</button>": "<span class=\"username\">{v0}</span>\n      <button class=\"btn small\" id=\"logout-btn\">Sign out</button>",
  "<a class=\"btn small\" href=\"#/login\">登录</a>": "<a class=\"btn small\" href=\"#/login\">Sign in</a>",
  "出错了": "Something went wrong",
  "未登录": "Not signed in",
  "请求失败": "Request failed",
};

const SUPPORTED = ["zh", "en"];

function detect() {
  const saved = localStorage.getItem("picshelf.lang");
  if (SUPPORTED.includes(saved)) return saved;
  return (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
}

let locale = detect();

export function getLocale() {
  return locale;
}

export function setLocale(l) {
  locale = SUPPORTED.includes(l) ? l : "en";
  localStorage.setItem("picshelf.lang", locale);
}

export function toggleLocale() {
  setLocale(locale === "zh" ? "en" : "zh");
  return locale;
}

export function t(msgid, vars) {
  let s = locale === "zh" ? msgid : (EN[msgid] ?? msgid);
  if (vars) {
    for (const k of Object.keys(vars)) {
      s = s.split(`{${k}}`).join(String(vars[k]));
    }
  }
  return s;
}
