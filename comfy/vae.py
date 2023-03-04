from typing import Optional, Union

import numpy as np
import torch
from PIL import Image
from torch import Tensor

from comfy.hazard.sd import VAE
from comfy.latent_image import LatentImage
from comfy.util import (
    _check_divisible_by_64,
    _image_to_greyscale_tensor,
    _image_to_rgb_tensor, SDType,
)


class VAEModel(SDType):
    def __init__(self, model: VAE, device: Union[str, torch.device] = "cpu"):
        self._model = model
        self.to(device)

    def to(self, device: Union[str, torch.device]) -> "VAEModel":
        """
        Modifies the object in-place.
        """
        torch_device = torch.device(device)
        if torch_device == self.device:
            return self

        self._model.first_stage_model.to(torch_device)
        self._model.device = torch_device
        self.device = torch_device
        return self

    @classmethod
    def from_model(cls, model_filepath: str) -> "VAEModel":
        # VAELoader
        return VAEModel(VAE(ckpt_path=model_filepath))

    @SDType.requires_cuda
    def encode(self, image: Image) -> LatentImage:
        # VAEEncode
        # XXX something's wrong here, I think
        img_t, _ = _image_to_rgb_tensor(image)
        img = self._model.encode(img_t)
        return LatentImage(img, device=self.device)

    @SDType.requires_cuda
    def masked_encode(self, image: Image, mask: Image) -> LatentImage:
        # VAEEncodeForInpaint
        img_t, img_size = _image_to_rgb_tensor(image)
        mask_t, mask_size = _image_to_greyscale_tensor(mask)
        assert img_size == mask_size
        _check_divisible_by_64(*img_size)

        kernel_tensor = torch.ones((1, 1, 6, 6))

        mask_erosion = torch.clamp(
            torch.nn.functional.conv2d(
                (1.0 - mask_t.round())[None], kernel_tensor, padding=3
            ),
            0,
            1,
        )

        for i in range(3):
            img_t[:, :, :, i] -= 0.5
            img_t[:, :, :, i] *= mask_erosion[0][:, :].round()
            img_t[:, :, :, i] += 0.5

        img = self._model.encode(img_t)
        return LatentImage(img, mask=mask_t, device=self.device)

    @SDType.requires_cuda
    def decode(self, latent_image: LatentImage) -> Image:
        # VAEDecode

        img: Tensor = self._model.decode(
            latent_image.to_internal_representation()["samples"]
        )
        if img.shape[0] != 1:
            raise RuntimeError(
                f"Expected the output of vae.decode to have shape[0]==1.  shape={img.shape}"
            )
        arr = img.detach().cpu().numpy().reshape(img.shape[1:])
        arr = (np.clip(arr, 0, 1) * 255).round().astype("uint8")
        # TODO return some wrapped tensor type, instead of going all the way to Pillow image
        return Image.fromarray(arr)
