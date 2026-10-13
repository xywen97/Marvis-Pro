import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marvis.runtime import Runtime
from marvis.server import serve
from marvis.capabilities import Capabilities
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory); work = root/'资料'; work.mkdir()
    runtime = Runtime(root/'data', config={'llm': {'env_file': str(root/'.env')}}, offline=True)
    runtime.update_settings({'roots': [str(work)]})
    engine = runtime.knowledge_engine; engine.close()
    samples = [('OpenViking 接入指南.md', '# 文档与长期记忆\nOpenViking 保存用户偏好与经验。文档知识库支持关键词搜索和语义检索，每个结果都能回到原文件。'), ('产品设计笔记.md', '# 简洁而清晰\n资料卡片展示文件来源、内容摘要和同步状态。用户可以预览，再让 AI 阅读与总结。'), ('项目架构.py', '# SQLite 保存执行记录\n# OpenViking 负责语义检索\nclass Knowledge: pass')]
    for name, text in samples: (work/name).write_text(text)
    for i in range(27): (work/f'研究笔记 {i+1:02d}.md').write_text(f'# 研究笔记 {i+1}\n检索与上下文管理的研究记录。\n知识资源使用独立空间，与对话记忆分别管理。')
    Capabilities(runtime.store).execute('knowledge_index', {'path': str(work)})
    for d in engine.documents():
        uri=engine.prefix+'/'+d['name']
        runtime.store.execute('INSERT INTO knowledge_resources VALUES (?,?,?,?,?,?,?)', (d['path'],engine.fingerprint(d),uri,'','ready','',time.time()))
    engine.available=lambda: True
    runtime.memory.client=Mock()
    runtime.memory.client.request.return_value={'resources':[{'uri':engine.prefix+'/OpenViking 接入指南.md','abstract':'OpenViking 提供文档资源语义检索与长期记忆。','score':.86}]}
    server,url=serve(runtime)
    print(json.dumps({'url':url,'root':str(work)}),flush=True)
    try:
        for line in sys.stdin:
            command=json.loads(line)
            if command.get('action')=='unavailable': engine.available=lambda: False
    finally:server.shutdown();server.server_close();runtime.close()
