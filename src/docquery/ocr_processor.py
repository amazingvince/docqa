import abc
import logging
from typing import List, Optional, Tuple, Any

import numpy as np


class NoOCRReaderFound(Exception):
    def __init__(self, e):
        self.e = e

    def __str__(self):
        return f"Could not load OCR Processor: {self.e}"


TESSERACT_AVAILABLE = False
EASYOCR_AVAILABLE = False

try:
    import pytesseract  # noqa

    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
except ImportError:
    pass
except pytesseract.TesseractNotFoundError as e:
    logging.warning("Unable to find tesseract: %s." % (e))
    pass

try:
    import easyocr  # noqa

    EASYOCR_AVAILABLE = True
except ImportError:
    pass


class OCRProcessor(metaclass=abc.ABCMeta):
    def __init__(self, device: Optional[int]):
        self.device = device
        self.check_if_available()

    @abc.abstractmethod
    def apply_ocr(self, image: "Image.Image") -> Tuple[(List[Any], List[List[int]])]:
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def check_if_available():
        raise NotImplementedError

    @staticmethod
    def normalize_box(box, width, height):
        return [
            max(min(c, 1000), 0)
            for c in [
                int(1000 * (box[0] / width)),
                int(1000 * (box[1] / height)),
                int(1000 * (box[2] / width)),
                int(1000 * (box[3] / height)),
            ]
        ]


class TesseractProcessor(OCRProcessor):
    def __init__(self):
        super().__init__()

    def apply_ocr(self, image: "Image.Image") -> tuple[list[Any], list[list[int]]]:
        """Applies Tesseract on a document image, and returns recognized words + normalized bounding boxes."""
        data = pytesseract.image_to_data(image, output_type="dict")
        words, left, top, width, height = data["text"], data["left"], data["top"], data["width"], data["height"]

        # filter empty words and corresponding coordinates
        irrelevant_indices = [idx for idx, word in enumerate(words) if not word.strip()]
        words = [word for idx, word in enumerate(words) if idx not in irrelevant_indices]
        left = [coord for idx, coord in enumerate(left) if idx not in irrelevant_indices]
        top = [coord for idx, coord in enumerate(top) if idx not in irrelevant_indices]
        width = [coord for idx, coord in enumerate(width) if idx not in irrelevant_indices]
        height = [coord for idx, coord in enumerate(height) if idx not in irrelevant_indices]

        # turn coordinates into (left, top, left+width, top+height) format
        actual_boxes = [[x, y, x + w, y + h] for x, y, w, h in zip(left, top, width, height)]

        image_width, image_height = image.size

        # finally, normalize the bounding boxes
        normalized_boxes = [self.normalize_box(box, image_width, image_height) for box in actual_boxes]

        assert len(words) == len(normalized_boxes), "Not as many words as there are bounding boxes"

        return words, normalized_boxes

    @staticmethod
    def check_if_available():
        if not TESSERACT_AVAILABLE:
            raise NoOCRReaderFound(
                "Unable to use pytesseract (OCR will be unavailable). Install tesseract to process images with OCR."
            )


class EasyOCRProcessor(OCRProcessor):
    def __init__(self):
        super().__init__()
        self.reader = None

    def apply_ocr(self, image: "Image.Image") -> tuple[list[Any], list[list[int]]]:
        """Applies Easy OCR on a document image, and returns recognized words + normalized bounding boxes."""
        if not self.reader:
            # TODO: expose language currently setting to english
            self.reader = easyocr.Reader(['en'], gpu=self.device > -1)

        # apply OCR
        data = self.reader.readtext(np.array(image))
        boxes, words, acc = list(map(list, zip(*data)))

        # filter empty words and corresponding coordinates
        irrelevant_indices = set(idx for idx, word in enumerate(words) if not word.strip())
        words = [word for idx, word in enumerate(words) if idx not in irrelevant_indices]
        boxes = [coords for idx, coords in enumerate(boxes) if idx not in irrelevant_indices]

        # turn coordinates into (left, top, left+width, top+height) format
        actual_boxes = [tl + br for tl, tr, br, bl in boxes]

        image_width, image_height = image.size

        # finally, normalize the bounding boxes
        normalized_boxes = [self.normalize_box(box, image_width, image_height) for box in actual_boxes]

        assert len(words) == len(normalized_boxes), "Not as many words as there are bounding boxes"

        return words, normalized_boxes

    @staticmethod
    def check_if_available():
        if not EASYOCR_AVAILABLE:
            raise NoOCRReaderFound(
                "Unable to use easyocr (OCR will be unavailable). Install easyocr to process images with OCR."
            )


class DummyProcessor(OCRProcessor):
    def __init__(self):
        super().__init__()
        self.reader = None

    def apply_ocr(self, image: "Image.Image") -> tuple[list[Any], list[list[int]]]:
        raise NoOCRReaderFound("Unable to find any OCR engine and OCR extraction was requested")

    @staticmethod
    def check_if_available():
        logging.warning("Unable to find OCR processor might not be needed")