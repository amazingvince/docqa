from collections import OrderedDict

from transformers import AutoConfig, AutoTokenizer, pipeline
from transformers.models.auto.auto_factory import _BaseAutoModelClass, _LazyAutoMapping
from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES
from transformers.models.layoutlm.configuration_layoutlm import LayoutLMConfig
from transformers.pipelines import PIPELINE_REGISTRY
import torch

from .ext.model import LayoutLMForQuestionAnswering
from .ext.pipeline import DocumentQuestionAnsweringPipeline



CHECKPOINT = "impira/layoutlm-document-qa"
REVISION = "02daaaf614d4ae08fa6a1d51693baaf7de819585"

MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING_NAMES = OrderedDict(
    [
        ("layoutlm", "LayoutLMForQuestionAnswering"),
        ("donut-swin", "DonutSwinModel"),
    ]
)

MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING = _LazyAutoMapping(
    CONFIG_MAPPING_NAMES, MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING_NAMES
)


class AutoModelForDocumentQuestionAnswering(_BaseAutoModelClass):
    _model_mapping = MODEL_FOR_DOCUMENT_QUESTION_ANSWERING_MAPPING


PIPELINE_REGISTRY.register_pipeline(
    "document-question-answering",
    pipeline_class=DocumentQuestionAnsweringPipeline,
    pt_model=AutoModelForDocumentQuestionAnswering,
)


def get_pipeline(checkpoint=None, revision=None, device=None):
    if checkpoint is None:
        checkpoint = CHECKPOINT

    if checkpoint == CHECKPOINT and revision is None:
        revision = REVISION
    
    if device is None:
        device = "cuda:0" if torch.cuda.is_available() else "cpu"

    kwargs = {}
    if checkpoint == CHECKPOINT:
        config = LayoutLMConfig.from_pretrained(checkpoint, revision=revision)
        kwargs["add_prefix_space"] = True
    else:
        config = AutoConfig.from_pretrained(checkpoint, revision=revision)

    tokenizer = AutoTokenizer.from_pretrained(
        checkpoint,
        revision=revision,
        config=config,
        **kwargs,
    )

    if checkpoint == CHECKPOINT:
        model = LayoutLMForQuestionAnswering.from_pretrained(checkpoint, revision=revision)
    else:
        model = checkpoint

    pipeline_kwargs = {device : device)}
    if config.model_type == "vision-encoder-decoder":
        pipeline_kwargs["feature_extractor"] = model

    return pipeline(
        "document-question-answering",
        revision=revision,
        model=model,
        tokenizer=tokenizer,
        **pipeline_kwargs,
    )
