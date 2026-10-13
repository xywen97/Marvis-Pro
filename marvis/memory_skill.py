"""Short memory-only requests take an audited path without file/tool planning."""
import re


def memory_intent(prompt):
    text = prompt.strip().rstrip('？?。！!').strip()
    if len(text) > 4000 or '\n' in text: return None
    # Restrict the fast path to a memory-only request, not a mixed action plan.
    if re.search(r'[，,；;](?:然后|再帮|并帮|同时|并且)', text): return None
    if re.match(r'^(?:请帮我|帮我|请)?(?:记住|记下|记好|保存我的(?:长期)?(?:记忆|偏好)|更新我的(?:长期)?(?:记忆|偏好))', text): return 'save'
    if re.fullmatch(r'(?:请问)?(?:我是谁|我叫什么(?:名字)?|我的(?:名字|姓名|称呼)是什么|你(?:还)?记得我是谁吗|我(?:比较)?喜欢吃什么(?:水果)?|我喜欢什么|我的(?:长期)?偏好(?:是什么|有哪些)|你(?:还)?记得我的(?:名字|姓名|偏好|喜好)吗)', text): return 'search'
    return None


def relevant_records(records, query):
    if re.search(r'是谁|名字|姓名|称呼|叫什么', query):
        return [r for r in records if r['uri'].endswith('/profile.md') or re.search(r'姓名|名字|称呼', r['text'])]
    if '吃' in query or '水果' in query:
        return [r for r in records if re.search(r'吃|水果|饮食|食物|口味|food|fruit|diet', r['text']+' '+r['uri'], re.I)]
    if re.search(r'喜欢|偏好|喜好', query):
        return [r for r in records if '/preferences/' in r['uri'] or re.search(r'喜欢|偏好|喜好', r['text'])]
    return records


def memory_reply(result, kind, query=''):
    status = result['status']
    if kind == 'save':
        if status == 'saved': return 'OpenViking 已完成长期记忆写入。'
        if status == 'processing': return '已提交后台保存，正在处理中，尚未确认保存完成。完成后界面的记忆状态会更新。'
        return result.get('message', '记忆保存未确认完成。') + ('\n' + result['error'] if result.get('error') else '')
    if status in ('disabled', 'unavailable'): return result['message']
    records = relevant_records(result.get('records', []), query)
    if records:
        # No model is configured: retain a short factual fallback rather than
        # dumping every retrieved document into the user-facing answer.
        text = re.sub(r'(?m)^\s*(?:#+\s*|[-*]\s*)', '', records[0]['text']).strip()[:300]
    else:
        text = '本次检索没有匹配到相关的长期记忆。'
    if status == 'processing': text += '\n\n还有信息正在保存，本次结果可能不完整；不能据此判断这些信息不存在。'
    if result.get('failed_saves'): text += '\n\n有历史记忆保存失败，可在任务记录中查看原因。'
    return text
