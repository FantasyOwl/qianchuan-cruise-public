import base64
import tempfile
import unittest
from pathlib import Path
from monitor.branding import BrandingStore,DEFAULT_BRANDING

PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jT1kAAAAASUVORK5CYII='


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ui-branding.json';self.store=BrandingStore(self.path)

    def tearDown(self):self.tmp.cleanup()

    def test_custom_name_icon_survive_reopen_and_default_restore(self):
        self.assertEqual(self.store.get(),DEFAULT_BRANDING)
        value={'name':'我的投放工作台','subtitle':'多户直播管理','icon_data_url':PNG}
        self.assertEqual(self.store.save(value),value)
        self.assertEqual(BrandingStore(self.path).get(),value)
        self.store.save(DEFAULT_BRANDING);self.assertEqual(self.store.get(),DEFAULT_BRANDING)

    def test_invalid_image_or_name_cannot_replace_saved_branding(self):
        self.store.save(DEFAULT_BRANDING)
        for patch in ({'name':''},{'name':'x'*33},{'name':'a\nb'},{'icon_data_url':'https://example.com/icon.svg'},
                      {'icon_data_url':'data:image/svg+xml;base64,PHN2Zy8+'},{'icon_data_url':'data:image/png;base64,AAAA'},
                      {'icon_data_url':PNG+'invalid'},{'extra':True}):
            with self.assertRaises(ValueError):self.store.save({**DEFAULT_BRANDING,**patch})
            self.assertEqual(self.store.get(),DEFAULT_BRANDING)
        raw=bytearray(base64.b64decode(PNG.split(',')[1]));raw[16:20]=(513).to_bytes(4,'big')
        with self.assertRaises(ValueError):self.store.save({**DEFAULT_BRANDING,'icon_data_url':'data:image/png;base64,'+base64.b64encode(raw).decode()})

    def test_corrupt_saved_file_recovers_default_without_changing_other_settings(self):
        self.path.write_text('bad json',encoding='utf-8')
        self.assertEqual(self.store.get(),DEFAULT_BRANDING)
        self.assertEqual(self.path.read_text(encoding='utf-8'),'bad json')
