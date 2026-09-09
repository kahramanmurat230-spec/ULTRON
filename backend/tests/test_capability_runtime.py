from app.core.capability_runtime import readiness_report


def test_readiness_report_is_consistent():
    report = readiness_report()
    assert report['total'] == len(report['capabilities'])
    assert report['implemented'] + report['adapter'] + report['planned'] == report['total']
    assert 0 <= report['percent'] <= 100
    assert report['planned'] == 0
