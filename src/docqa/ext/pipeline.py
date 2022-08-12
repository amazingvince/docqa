# NOTE: This code is currently under review for inclusion in the main
# huggingface/transformers repository:
# https://github.com/huggingface/transformers/pull/18414
from typing import List, Optional, Tuple, Union

import numpy as np
from transformers.pipelines.base import PIPELINE_INIT_ARGS, ChunkPipeline
from transformers.utils import add_end_docstrings, is_torch_available, logging

from .qa_helpers import TESSERACT_LOADED, VISION_LOADED, Image, load_image, pytesseract, select_starts_ends


if is_torch_available():
    import torch

    # We do not perform the check in this version of the pipeline code
    # from transformers.models.auto.modeling_auto import MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING

logger = logging.get_logger(__name__)


# normalize_bbox() and apply_tesseract() are derived from apply_tesseract in models/layoutlmv3/feature_extraction_layoutlmv3.py.
# However, because the pipeline may evolve from what layoutlmv3 currently does, it's copied (vs. imported) to avoid creating an
# unecessary dependency.
def normalize_box(box, width, height):
    return [
        int(1000 * (box[0] / width)),
        int(1000 * (box[1] / height)),
        int(1000 * (box[2] / width)),
        int(1000 * (box[3] / height)),
    ]


def apply_tesseract(image: "Image.Image", lang: Optional[str], tesseract_config: Optional[str]):
    """Applies Tesseract OCR on a document image, and returns recognized words + normalized bounding boxes."""
    # apply OCR
    data = pytesseract.image_to_data(image, lang=lang, output_type="dict", config=tesseract_config)
    words, left, top, width, height = data["text"], data["left"], data["top"], data["width"], data["height"]

    # filter empty words and corresponding coordinates
    irrelevant_indices = [idx for idx, word in enumerate(words) if not word.strip()]
    words = [word for idx, word in enumerate(words) if idx not in irrelevant_indices]
    left = [coord for idx, coord in enumerate(left) if idx not in irrelevant_indices]
    top = [coord for idx, coord in enumerate(top) if idx not in irrelevant_indices]
    width = [coord for idx, coord in enumerate(width) if idx not in irrelevant_indices]
    height = [coord for idx, coord in enumerate(height) if idx not in irrelevant_indices]

    # turn coordinates into (left, top, left+width, top+height) format
    actual_boxes = []
    for x, y, w, h in zip(left, top, width, height):
        actual_box = [x, y, x + w, y + h]
        actual_boxes.append(actual_box)

    image_width, image_height = image.size

    # finally, normalize the bounding boxes
    normalized_boxes = []
    for box in actual_boxes:
        normalized_boxes.append(normalize_box(box, image_width, image_height))

    assert len(words) == len(normalized_boxes), "Not as many words as there are bounding boxes"

    return words, normalized_boxes


@add_end_docstrings(PIPELINE_INIT_ARGS)
class DocumentQuestionAnsweringPipeline(ChunkPipeline):
    # TODO: Update task_summary docs to include an example with document QA and then update the first sentence
    """
    Document Question Answering pipeline using any `AutoModelForDocumentQuestionAnswering`. See the [question answering
    examples](../task_summary#question-answering) for more information.

    This document question answering pipeline can currently be loaded from [`pipeline`] using the following task
    identifier: `"document-question-answering"`.

    The models that this pipeline can use are models that have been fine-tuned on a document question answering task.
    See the up-to-date list of available models on
    [huggingface.co/models](https://huggingface.co/models?filter=document-question-answering).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.check_model_type(MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING)

    def _sanitize_parameters(
        self,
        padding=None,
        doc_stride=None,
        max_question_len=None,
        lang: Optional[str] = None,
        tesseract_config: Optional[str] = None,
        max_answer_len=None,
        max_seq_len=None,
        top_k=None,
        handle_impossible_answer=None,
        **kwargs,
    ):
        preprocess_params, postprocess_params = {}, {}
        if padding is not None:
            preprocess_params["padding"] = padding
        if doc_stride is not None:
            preprocess_params["doc_stride"] = doc_stride
        if max_question_len is not None:
            preprocess_params["max_question_len"] = max_question_len
        if max_seq_len is not None:
            preprocess_params["max_seq_len"] = max_seq_len
        if lang is not None:
            preprocess_params["lang"] = lang
        if tesseract_config is not None:
            preprocess_params["tesseract_config"] = tesseract_config

        if top_k is not None:
            if top_k < 1:
                raise ValueError(f"top_k parameter should be >= 1 (got {top_k})")
            postprocess_params["top_k"] = top_k
        if max_answer_len is not None:
            if max_answer_len < 1:
                raise ValueError(f"max_answer_len parameter should be >= 1 (got {max_answer_len}")
            postprocess_params["max_answer_len"] = max_answer_len
        if handle_impossible_answer is not None:
            postprocess_params["handle_impossible_answer"] = handle_impossible_answer

        return preprocess_params, {}, postprocess_params

    def __call__(
        self,
        image: Union["Image.Image", str],
        question: Optional[str] = None,
        word_boxes: Tuple[str, List[float]] = None,
        **kwargs,
    ):
        """
        Answer the question(s) given as inputs by using the document(s). A document is defined as an image and an
        optional list of (word, box) tuples which represent the text in the document. If the `word_boxes` are not
        provided, it will use the Tesseract OCR engine (if available) to extract the words and boxes automatically.

        You can invoke the pipeline several ways:

        - `pipeline(image=image, question=question)`
        - `pipeline(image=image, question=question, word_boxes=word_boxes)`
        - `pipeline([{"image": image, "question": question}])`
        - `pipeline([{"image": image, "question": question, "word_boxes": word_boxes}])`

        Args:
            image (`str` or `PIL.Image`):
                The pipeline handles three types of images:

                - A string containing a http link pointing to an image
                - A string containing a local path to an image
                - An image loaded in PIL directly

                The pipeline accepts either a single image or a batch of images. If given a single image, it can be
                broadcasted to multiple questions.
            question (`str`):
                A question to ask of the document.
            word_boxes (`List[str, Tuple[float, float, float, float]]`, *optional*):
                A list of words and bounding boxes (normalized 0->1000). If you provide this optional input, then the
                pipeline will use these words and boxes instead of running OCR on the image to derive them. This allows
                you to reuse OCR'd results across many invocations of the pipeline without having to re-run it each
                time.
            top_k (`int`, *optional*, defaults to 1):
                The number of answers to return (will be chosen by order of likelihood). Note that we return less than
                top_k answers if there are not enough options available within the context.
            doc_stride (`int`, *optional*, defaults to 128):
                If the words in the document are too long to fit with the question for the model, it will be split in
                several chunks with some overlap. This argument controls the size of that overlap.
            max_answer_len (`int`, *optional*, defaults to 15):
                The maximum length of predicted answers (e.g., only answers with a shorter length are considered).
            max_seq_len (`int`, *optional*, defaults to 384):
                The maximum length of the total sentence (context + question) in tokens of each chunk passed to the
                model. The context will be split in several chunks (using `doc_stride` as overlap) if needed.
            max_question_len (`int`, *optional*, defaults to 64):
                The maximum length of the question after tokenization. It will be truncated if needed.
            handle_impossible_answer (`bool`, *optional*, defaults to `False`):
                Whether or not we accept impossible as an answer.
            lang (`str`, *optional*):
                Language to use while running OCR. Defaults to english.
            tesseract_config (`str`, *optional*):
                Additional flags to pass to tesseract while running OCR.

        Return:
            A `dict` or a list of `dict`: Each result comes as a dictionary with the following keys:

            - **score** (`float`) -- The probability associated to the answer.
            - **start** (`int`) -- The start word index of the answer (in the OCR'd version of the input or provided
              `word_boxes`).
            - **end** (`int`) -- The end word index of the answer (in the OCR'd version of the input or provided
              `word_boxes`).
            - **answer** (`str`) -- The answer to the question.
        """
        if isinstance(question, str):
            inputs = {"question": question, "image": image}
            if word_boxes is not None:
                inputs["word_boxes"] = word_boxes
        else:
            inputs = image
        return super().__call__(inputs, **kwargs)

    def preprocess(
        self,
        input,
        padding="do_not_pad",
        doc_stride=None,
        max_question_len=64,
        max_seq_len=None,
        word_boxes: Tuple[str, List[float]] = None,
        lang=None,
        tesseract_config="",
    ):
        # NOTE: This code mirrors the code in question answering and will be implemented in a follow up PR
        # to support documents with enough tokens that overflow the model's window
        if max_seq_len is None:
            # TODO: LayoutLM's stride is 512 by default. Is it ok to use that as the min
            # instead of 384 (which the QA model uses)?
            max_seq_len = min(self.tokenizer.model_max_length, 512)

        if doc_stride is None:
            doc_stride = min(max_seq_len // 2, 128)

        image = None
        image_features = {}
        if input.get("image", None) is not None:
            if not VISION_LOADED:
                raise ValueError(
                    "If you provide an image, then the pipeline will run process it with PIL (Pillow), but"
                    " PIL is not available. Install it with pip install Pillow."
                )
            image = load_image(input["image"])
            if self.feature_extractor is not None:
                image_features.update(self.feature_extractor(images=image, return_tensors=self.framework))

        words, boxes = None, None
        if "word_boxes" in input:
            words = [x[0] for x in input["word_boxes"]]
            boxes = [x[1] for x in input["word_boxes"]]
        elif "words" in image_features and "boxes" in image_features:
            words = image_features.pop("words")
            boxes = image_features.pop("boxes")
        elif image is not None:
            if not TESSERACT_LOADED:
                raise ValueError(
                    "If you provide an image without word_boxes, then the pipeline will run OCR using Tesseract, but"
                    " pytesseract is not available. Install it with pip install pytesseract."
                )
            words, boxes = apply_tesseract(image, lang=lang, tesseract_config=tesseract_config)
        else:
            raise ValueError(
                "You must provide an image or word_boxes. If you provide an image, the pipeline will automatically run"
                " OCR to derive words and boxes"
            )

        if self.tokenizer.padding_side != "right":
            raise ValueError(
                "Document question answering only supports tokenizers whose padding side is 'right', not"
                f" {self.tokenizer.padding_side}"
            )

        encoding = self.tokenizer(
            text=input["question"].split(),
            text_pair=words,
            padding=padding,
            max_length=max_seq_len,
            stride=doc_stride,
            return_token_type_ids=True,
            is_split_into_words=True,
            truncation="only_second",
            return_overflowing_tokens=True,
        )

        num_spans = len(encoding["input_ids"])

        # p_mask: mask with 1 for token than cannot be in the answer (0 for token which can be in an answer)
        # We put 0 on the tokens from the context and 1 everywhere else (question and special tokens)
        # This logic mirrors the logic in the question_answering pipeline
        p_mask = [[tok != 1 for tok in encoding.sequence_ids(span_id)] for span_id in range(num_spans)]

        for span_idx in range(num_spans):
            if self.framework == "pt":
                span_encoding = {k: torch.tensor(v[span_idx : span_idx + 1]) for (k, v) in encoding.items()}
                span_encoding.update(
                    {k: v for (k, v) in image_features.items()}
                )  # TODO: Verify cardinality is correct
            else:
                raise ValueError("Unsupported: Tensorflow preprocessing for DocumentQuestionAnsweringPipeline")

            input_ids_span_idx = encoding["input_ids"][span_idx]
            # keep the cls_token unmasked (some models use it to indicate unanswerable questions)
            if self.tokenizer.cls_token_id is not None:
                cls_indices = np.nonzero(np.array(input_ids_span_idx) == self.tokenizer.cls_token_id)[0]
                for cls_index in cls_indices:
                    p_mask[span_idx][cls_index] = 0

            # For each span, place a bounding box [0,0,0,0] for question and CLS tokens, [1000,1000,1000,1000]
            # for SEP tokens, and the word's bounding box for words in the original document.
            bbox = []
            for i, s, w in zip(
                encoding.input_ids[span_idx],
                encoding.sequence_ids(span_idx),
                encoding.word_ids(span_idx),
            ):
                if s == 1:
                    bbox.append(boxes[w])
                elif i == self.tokenizer.sep_token_id:
                    bbox.append([1000] * 4)
                else:
                    bbox.append([0] * 4)

            span_encoding["bbox"] = torch.tensor(bbox).unsqueeze(0)

            yield {
                **span_encoding,
                "p_mask": p_mask[span_idx],
                "word_ids": encoding.word_ids(span_idx),
                "words": words,
            }

    def _forward(self, model_inputs):
        p_mask = model_inputs.pop("p_mask", None)
        word_ids = model_inputs.pop("word_ids", None)
        words = model_inputs.pop("words", None)
        model_inputs.pop("overflow_to_sample_mapping")

        model_outputs = self.model(**model_inputs)

        model_outputs["p_mask"] = p_mask
        model_outputs["word_ids"] = word_ids
        model_outputs["words"] = words
        model_outputs["attention_mask"] = model_inputs["attention_mask"]
        return model_outputs

    def postprocess(self, model_outputs, top_k=1, handle_impossible_answer=False, max_answer_len=15):
        min_null_score = 1000000  # large and positive
        answers = []

        for output in model_outputs:
            words = output["words"]

            # TODO: Currently, we expect the length of model_outputs to be 1, because we do not stride
            # in the preprocessor code. When we implement that, we'll either need to handle tensors of size
            # > 1 or use the ChunkPipeline and handle multiple outputs (each of size = 1).
            starts, ends, scores, min_null_score = select_starts_ends(
                output["start_logits"],
                output["end_logits"],
                output["p_mask"],
                output["attention_mask"].numpy() if output.get("attention_mask", None) is not None else None,
                min_null_score,
                top_k,
                handle_impossible_answer,
                max_answer_len,
            )

            word_ids = output["word_ids"]
            for s, e, score in zip(starts, ends, scores):
                word_start, word_end = word_ids[s], word_ids[e]
                if word_start is not None and word_end is not None:
                    answers.append(
                        {
                            "score": score,
                            "answer": " ".join(words[word_start : word_end + 1]),
                            "start": word_start,
                            "end": word_end,
                        }
                    )

        if handle_impossible_answer:
            answers.append({"score": min_null_score, "answer": "", "start": 0, "end": 0})

        answers = sorted(answers, key=lambda x: x["score"], reverse=True)[:top_k]
        if len(answers) == 1:
            return answers[0]
        return answers