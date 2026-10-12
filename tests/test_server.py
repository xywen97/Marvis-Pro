import json
import tempfile
import unittest
import urllib.request
import urllib.error
from urllib.parse import urlparse,parse_qs
from urllib.parse import quote
from pathlib import Path
import base64
from marvis.runtime import Runtime
from marvis.server import serve


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.runtime=Runtime(self.tmp.name,offline=True, config={'evaluation': {'enabled': False}})
        self.server,self.url=serve(self.runtime);parsed=urlparse(self.url)
        self.base=f'http://{parsed.netloc}';self.token=parse_qs(parsed.fragment)['token'][0]
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.runtime.close();self.tmp.cleanup()
    def request(self,path,data=None,token=None,origin=None):
        headers={'X-Marvis-Token':token or self.token}
        if origin:headers['Origin']=origin
        if data is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(request) as response:return response.status,response.read(),response.headers
    def test_ui_and_state(self):
        status,body,headers=self.request('/')
        self.assertEqual(status,200);self.assertIn('工作台',body.decode());self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        _,body,_=self.request('/api/state');self.assertEqual(len(json.loads(body)['agents']),9)
        for path in ('/markdown.js','/vendor/marked.js','/vendor/purify.js','/vendor/highlight.js','/vendor/highlight.css'):
            self.assertEqual(self.request(path)[0],200)
    def test_image_requires_authorization_and_valid_raster(self):
        with tempfile.TemporaryDirectory() as root:
            self.runtime.update_settings({'roots':[root]})
            image=Path(root)/'test.png'
            pixels=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            image.write_bytes(pixels);path='/api/image?path='+quote(str(image))
            _,body,_=self.request(path);result=json.loads(body)
            self.assertEqual(result['mime'],'image/png');self.assertEqual(base64.b64decode(result['data']),pixels)
            with self.assertRaises(urllib.error.HTTPError):self.request(path,token='bad')
            with self.assertRaises(urllib.error.HTTPError):self.request('/api/image?path='+quote(str(Path(root).parent/'outside.png')))
            image.write_text('<svg onload="alert(1)"></svg>')
            with self.assertRaises(urllib.error.HTTPError):self.request(path)
            image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*(10*1024*1024))
            with self.assertRaises(urllib.error.HTTPError):self.request(path)
    def test_auth_and_cross_origin_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/state',token='bad')
        self.assertEqual(ctx.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/live',token='bad')
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/tasks',{'prompt':'x'},origin='https://evil.example')
        self.assertEqual(ctx.exception.code,403)
    def test_live_pushes_state_changes_before_connection_ends(self):
        request=urllib.request.Request(self.base+'/api/live',headers={'X-Marvis-Token':self.token})
        with urllib.request.urlopen(request,timeout=3) as response:
            self.assertIn('text/event-stream',response.headers['Content-Type'])
            first=response.readline().decode();self.assertTrue(first.startswith('data: '))
            self.assertEqual(json.loads(first[6:])['sessions'],[])
            self.assertEqual(response.readline(),b'\n')
            sid=self.runtime.new_session()
            updated=json.loads(response.readline().decode()[6:])
            self.assertEqual(updated['sessions'][0]['id'],sid)
    def test_sessions_and_task_submission(self):
        _,body,_=self.request('/api/sessions',{});sid=json.loads(body)['id']
        _,body,_=self.request('/api/tasks',{'prompt':'检查磁盘空间','session_id':sid})
        self.assertEqual(json.loads(body)['session_id'],sid)
        _,body,_=self.request('/api/messages?session='+sid);self.assertEqual(json.loads(body)[0]['role'],'user')

    def test_session_archive_restore_delete_api(self):
        sid=self.runtime.new_session();self.runtime.store.add_message(sid,'user','保留历史')
        self.request('/api/session-action',{'session_id':sid,'action':'archive'})
        _,body,_=self.request('/api/state');value=json.loads(body)
        self.assertEqual(value['sessions'],[]);self.assertEqual(value['archived_sessions'][0]['id'],sid)
        self.request('/api/session-action',{'session_id':sid,'action':'restore'})
        self.assertEqual(self.runtime.messages(sid)[0]['content'],'保留历史')
        self.request('/api/session-action',{'session_id':sid,'action':'delete'})
        self.assertEqual(self.runtime.sessions(),[])
