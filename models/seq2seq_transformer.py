import torch
import torch.nn as nn
import torch.optim as optim
import pytorch_lightning as pl
from torchmetrics import Accuracy
from typing import Tuple
from torch import Tensor
from layers.seq2seq_transformer_layers import PositionalEncoding, TokenEmbedding
from utilities.constant import SAVE_MODELS_PTH


class Seq2Seq(pl.LightningModule):
    def __init__(
        self,
        src_vocab_size: int,
        tgt_vocab_size: int,
        num_layers=6,
        emb_size=512,
        maxlen=140,
        padding_idx=0,
        eos_idx=2,
        learning_ratio=0.0001,
        output_filename="",
    ) -> None:
        super().__init__()

        # フィールド値の定義
        self.src_vocab_size = src_vocab_size
        self.tgt_vocab_size = tgt_vocab_size
        self.emb_size = emb_size
        self.d_model = emb_size
        self.nhead = self.d_model // 64
        self.maxlen = maxlen
        self.padding_idx = padding_idx
        self.eos_idx = eos_idx
        self.learning_ratio = learning_ratio
        self.output_filename = output_filename

        # レイヤーの定義
        self.src_tok_emb = TokenEmbedding(
            src_vocab_size, self.emb_size, padding_idx=self.padding_idx
        )
        self.tgt_tok_emb = TokenEmbedding(
            tgt_vocab_size, self.emb_size, padding_idx=self.padding_idx
        )
        self.pe = PositionalEncoding(
            self.d_model, max_len=self.maxlen, device=self.device
        )
        encoder_layer = nn.TransformerEncoderLayer(self.d_model, self.nhead)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers)
        decoder_layer = nn.TransformerDecoderLayer(self.d_model, self.nhead)
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers)
        self.generater = nn.Linear(self.d_model, tgt_vocab_size)

        # 損失関数の定義
        self.criterion = nn.CrossEntropyLoss(ignore_index=self.padding_idx)

        # 評価手法
        self.test_acc = Accuracy()

    def encode(self, src: Tensor, src_mask: Tensor):
        return self.encoder(self.pe(self.src_tok_emb(src)), src_mask)

    def decode(self, tgt: Tensor, memory: Tensor, tgt_mask: Tensor):
        return self.decoder(self.pe(self.tgt_tok_emb(tgt)), memory, tgt_mask)

    def forward(self, source: Tensor, target: Tensor):
        tgt_input = target[:-1, :]
        src_emb_pe = self.pe(self.src_tok_emb(source))
        tgt_emb_pe = self.pe(self.tgt_tok_emb(tgt_input))
        src_mask, src_padding_mask = self._create_src_mask(source)
        tgt_mask, tgt_padding_mask = self._create_tgt_mask(tgt_input)

        memory = self.encoder(src_emb_pe, src_mask, src_padding_mask)
        out = self.decoder(
            tgt_emb_pe, memory, tgt_mask, None, tgt_padding_mask, src_padding_mask
        )
        out = self.generater(out)

        return out

    def _create_src_mask(self, src: Tensor):
        src_size = src.shape[0]
        src_mask = torch.zeros((src_size, src_size), device=self.device).type(
            torch.bool
        )
        src_padding_mask = (src == self.padding_idx).transpose(0, 1)
        return src_mask.to(self.device), src_padding_mask.to(self.device)

    def _create_tgt_mask(self, tgt: Tensor):
        tgt_size = tgt.shape[0]
        tgt_mask = self._generate_square_subsequent_mask(tgt_size)
        tgt_padding_mask = (tgt == self.padding_idx).transpose(0, 1).to(self.device)
        return tgt_mask, tgt_padding_mask

    def _generate_square_subsequent_mask(self, sz: int):
        mask = torch.triu(torch.ones(sz, sz) == 1).transpose(0, 1)
        mask = mask.float()
        mask = mask.masked_fill_(mask == 0, float("-inf"))
        mask = mask.masked_fill_(mask == 1, float(0.0))
        return mask.to(self.device)

    def compute_loss(self, preds: Tensor, target: Tensor):
        preds = preds.reshape(-1, preds.shape[-1])
        target = target.reshape(-1)
        loss = self.criterion(preds, target)
        return loss

    def compute_acc(self, preds: Tensor, target: Tensor, acc):
        preds = preds.reshape(-1, preds.shape[-1])
        target = target.reshape(-1)
        return acc(preds, target)

    def training_step(self, batch: Tuple[Tensor, Tensor], batch_idx: int):
        x, t = batch
        batch_size = x.size(0)
        tgt_out = t[1:, :]
        preds = self.forward(x, t)

        loss = self.compute_loss(preds, tgt_out)
        self.log(
            "train_loss",
            loss,
            on_step=False,
            batch_size=batch_size,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )

        return loss

    def validation_step(self, batch: Tuple[Tensor, Tensor], batch_idx: int):
        x, t = batch
        batch_size = x.size(0)
        tgt_out = t[1:, :]
        preds = self.forward(x, t)

        loss = self.compute_loss(preds, tgt_out)

        self.log(
            "val_loss",
            value=loss,
            batch_size=batch_size,
            on_step=True,
            on_epoch=True,
            prog_bar=False,
            logger=True,
        )

        return loss

    def training_epoch_end(self, outputs):
        if self.current_epoch % 5 == 0:
            model_path = "output/{}model_epoch{}.pth".format(
                self.output_filename, self.current_epoch
            )
            torch.save(self.state_dict(), model_path)

    def test_step(self, batch: Tuple[Tensor, Tensor], batch_idx: int):
        x, t = batch
        batch_size = x.size(0)
        tgt_out = t[1:, :]
        preds = self.forward(x, t)

        loss = self.compute_loss(preds, tgt_out)
        acc = self.compute_acc(preds, tgt_out, self.test_acc)
        self.log(
            "test_loss",
            loss,
            batch_size=batch_size,
            on_step=True,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )
        self.log(
            "test_acc",
            acc,
            batch_size=batch_size,
            on_step=True,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )

        return loss

    def configure_optimizers(self):
        return optim.Adam(
            self.parameters(), lr=self.learning_ratio, betas=(0.9, 0.98), eps=1e-9
        )

