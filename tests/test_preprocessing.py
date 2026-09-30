import numpy as np
import cv2
import pytest
from instrument_reader.core.preprocessing import PreprocessingConfig, PreprocessingPipeline

def test_preprocessing_config_defaults():
    cfg = PreprocessingConfig()
    assert cfg.upscale_factor == 1
    assert cfg.morphology_shape == "rect"
    assert cfg.masks == []
    assert cfg.morphology_kernel == 3

def test_preprocessing_config_from_dict():
    data = {
        "grayscale": False,
        "upscale_factor": 2,
        "morphology_shape": "cross",
        "masks": [{"type": "rect", "coords": [10, 10, 20, 20], "color": 0}],
        "non_existent_key": 999  # Should be ignored safely
    }
    cfg = PreprocessingConfig.from_dict(data)
    assert cfg.grayscale is False
    assert cfg.upscale_factor == 2
    assert cfg.morphology_shape == "cross"
    assert len(cfg.masks) == 1
    assert not hasattr(cfg, "non_existent_key")

def test_upscale_factor():
    pipeline = PreprocessingPipeline()
    img = np.ones((50, 100, 3), dtype=np.uint8) * 100

    cfg_1x = PreprocessingConfig(grayscale=False, upscale_factor=1)
    res_1x = pipeline.process(img, cfg_1x)
    assert res_1x.shape[:2] == (50, 100)

    cfg_2x = PreprocessingConfig(grayscale=False, upscale_factor=2)
    res_2x = pipeline.process(img, cfg_2x)
    assert res_2x.shape[:2] == (100, 200)

    cfg_4x = PreprocessingConfig(grayscale=False, upscale_factor=4)
    res_4x = pipeline.process(img, cfg_4x)
    assert res_4x.shape[:2] == (200, 400)

def test_morphology_shapes():
    pipeline = PreprocessingPipeline()
    # Create an image with a single pixel dot
    img = np.zeros((30, 30), dtype=np.uint8)
    img[15, 15] = 255

    # 1. Vertical morphology
    cfg_vert = PreprocessingConfig(
        grayscale=False,
        threshold_method="none",
        morphology_op="dilate",
        morphology_shape="vertical",
        morphology_kernel=5
    )
    res_vert = pipeline.process(img, cfg_vert)
    # Height of dilation should be extended, width remains 1 pixel
    col = res_vert[:, 15]
    row = res_vert[15, :]
    assert np.count_nonzero(col) == 5
    assert np.count_nonzero(row) == 1

    # 2. Horizontal morphology
    cfg_horiz = PreprocessingConfig(
        grayscale=False,
        threshold_method="none",
        morphology_op="dilate",
        morphology_shape="horizontal",
        morphology_kernel=5
    )
    res_horiz = pipeline.process(img, cfg_horiz)
    col = res_horiz[:, 15]
    row = res_horiz[15, :]
    assert np.count_nonzero(col) == 1
    assert np.count_nonzero(row) == 5

    # 3. Cross morphology
    cfg_cross = PreprocessingConfig(
        grayscale=False,
        threshold_method="none",
        morphology_op="dilate",
        morphology_shape="cross",
        morphology_kernel=3
    )
    res_cross = pipeline.process(img, cfg_cross)
    # Center pixel plus 4-neighbors
    assert res_cross[15, 15] == 255
    assert res_cross[14, 15] == 255
    assert res_cross[16, 15] == 255
    assert res_cross[15, 14] == 255
    assert res_cross[15, 16] == 255
    # Corners should be zero for cross
    assert res_cross[14, 14] == 0
    assert res_cross[14, 16] == 0

def test_manual_masks():
    pipeline = PreprocessingPipeline()
    # White image
    img = np.ones((100, 100), dtype=np.uint8) * 255

    # Add black mask at [10, 20, 30, 40]
    cfg = PreprocessingConfig(
        grayscale=False,
        masks=[{"type": "rect", "coords": [10, 20, 30, 40], "color": 0}]
    )
    res = pipeline.process(img, cfg)
    # Mask area must be black (0)
    assert np.all(res[20:60, 10:40] == 0)
    # Outside mask must remain white (255)
    assert res[0, 0] == 255
    assert res[19, 9] == 255

def test_manual_masks_with_upscaling():
    pipeline = PreprocessingPipeline()
    # 50x50 image
    img = np.ones((50, 50), dtype=np.uint8) * 255

    # Mask in 50x50 space at [10, 10, 20, 20]
    # With upscale_factor=2, mask should cover [20, 20, 40, 40] in 100x100 space
    cfg = PreprocessingConfig(
        grayscale=False,
        upscale_factor=2,
        masks=[{"type": "rect", "coords": [10, 10, 20, 20], "color": 0}]
    )
    res = pipeline.process(img, cfg)
    assert res.shape[:2] == (100, 100)
    assert np.all(res[20:60, 20:60] == 0)
    assert res[0, 0] == 255
