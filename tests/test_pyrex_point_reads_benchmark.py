import pytest

from benchmarks.pyrex_point_reads import measure


@pytest.mark.parametrize("option", ["nodes", "reads", "trials"])
def test_invalid_measurement_size(option):
    with pytest.raises(ValueError):
        measure(**{option: 0})


def test_negative_warmup():
    with pytest.raises(ValueError):
        measure(warmup=-1)


def test_point_read_benchmark_smoke():
    pytest.importorskip("pyrex")
    report = measure(nodes=3, reads=6, warmup=1, trials=2)
    assert report["pyrex_version"]
    assert report["serializer"] == "json"
    assert set(report["results"]) == {"default", "transactional"}
    for result in report["results"].values():
        assert set(result) == {"native_get", "store_get_node", "graph_get_node"}
        for metrics in result.values():
            assert len(metrics["samples_us"]) == 2
            assert 0 < metrics["min_us"] <= metrics["median_us"] <= metrics["max_us"]
