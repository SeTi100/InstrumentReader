from instrument_reader.core.calculation import CalculationChannel, CalculationResult
from instrument_reader.gui.live_plot import LivePlotWidget, is_rate_result
from instrument_reader.gui.main_window import MainWindow


def _res(name, formula, value, unit="g/h", valid=True):
    return CalculationResult(
        channel_id=name, name=name, value=value, unit=unit,
        formula=formula, is_valid=valid,
    )


def test_is_rate_result_detects_rate_and_slope():
    assert is_rate_result(_res("a", "rate(m, 10) * 3600", 1.0))
    assert is_rate_result(_res("b", "slope( Waage , 5)", 1.0))
    assert not is_rate_result(_res("c", "m * 2", 1.0))
    assert not is_rate_result(_res("d", "", 1.0))


def test_live_plot_collects_mass_and_rate_series(qtbot):
    w = LivePlotWidget()
    qtbot.addWidget(w)
    w.show()

    for i in range(5):
        w.update_data(
            100.0 + i, "Waage", 50.0 - 0.1 * i, 50.0 - 0.1 * i,
            [_res("Massenstrom", "rate(m, 10) * 3600", -360.0), _res("Doppelt", "m * 2", 1.0)],
        )

    assert [v for _, v in w.mass_data["Waage"]][-1] == 49.6
    assert len(w.mass_data["Waage_korrigiert"]) == 5
    assert list(w.rate_data) == ["Massenstrom"]
    assert w.rate_units["Massenstrom"] == "g/h"

    xs, ys = w._curves[("mass", "Waage")].getData()
    assert list(xs) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert ys[0] == 50.0
    assert "49.600" in w.mass_label.text()
    assert "Massenstrom" in w.rate_label.text() and "g/h" in w.rate_label.text()


def test_live_plot_skips_invalid_values_and_respects_window(qtbot):
    w = LivePlotWidget()
    qtbot.addWidget(w)
    w.show()
    w.window_combo.setCurrentIndex(0)  # 1 min

    w.update_data(0.0, "Waage", None, None, [_res("R", "rate(m, 5)", None, valid=False)])
    for t in range(1, 91):
        w.update_data(float(t), "Waage", float("nan") if t == 3 else 10.0, None, [_res("R", "rate(m, 5)", 0.5)])

    assert "R" in w.rate_data and len(w.rate_data["R"]) == 90
    assert len(w.mass_data["Waage"]) == 89  # NaN dropped, None dropped

    xs, _ = w._curves[("mass", "Waage")].getData()
    assert min(xs) >= 30.0  # only the last 60 s are drawn

    w.clear()
    assert not w.mass_data and not w.rate_data and not w._curves


def test_live_plot_restarts_when_time_goes_backwards(qtbot):
    w = LivePlotWidget()
    qtbot.addWidget(w)
    w.show()
    for t in (50.0, 51.0, 52.0):
        w.update_data(t, "Waage", 10.0, None, [])
    w.update_data(0.0, "Waage", 11.0, None, [])
    assert list(w.mass_data["Waage"]) == [(0.0, 11.0)]
    xs, _ = w._curves[("mass", "Waage")].getData()
    assert list(xs) == [0.0]


def test_main_window_feeds_live_plot(tmp_path, qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = str(tmp_path / "live_plot.db")
    win.db_writer.db._init_db()
    win.show()

    win.calc_engine.add_channel(CalculationChannel(
        name="Massenstrom", unit="g/s", formula="rate(m, 10)", variables={"m": "Waage"},
    ))

    for i in range(4):
        readings = [{
            "roi_id": "r1", "roi_name": "Waage", "raw_text": "", "parsed_value": 100.0 - 0.01 * i,
            "unit": "g", "confidence": 1.0, "is_valid": True,
        }]
        win.on_readings_ready(readings, timestamp=1000.0 + i)

    assert len(win.live_plot.mass_data["Waage"]) == 4
    assert "Waage_korrigiert" in win.live_plot.mass_data
    assert "Massenstrom" in win.live_plot.rate_data

    win.seek_video(5.0)
    assert not win.live_plot.mass_data

    assert win.live_plot.height() <= 200  # compact strip

    win.live_plot_act.setChecked(False)
    assert not win.live_plot.isVisible()
