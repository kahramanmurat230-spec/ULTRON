from app.multimodal.camera_adapter import CameraAdapter
from app.multimodal.pdf_workspace import PDFWorkspace


def test_camera_adapter_never_fakes_availability(monkeypatch):
    monkeypatch.setitem(__import__('sys').modules, 'cv2', None)
    result = CameraAdapter().status()
    assert result['available'] is False
    assert result['permission_required'] is True


def test_pdf_workspace_reports_real_backend_status():
    result = PDFWorkspace().status()
    assert 'available' in result
    if result['available']:
        assert result['backend'] == 'pypdf'


def test_pdf_workspace_rejects_non_pdf(tmp_path):
    p = tmp_path / 'note.txt'
    p.write_text('hello', encoding='utf-8')
    assert PDFWorkspace().inspect(str(p)) == {'ok': False, 'error': 'path is not a PDF file'}
