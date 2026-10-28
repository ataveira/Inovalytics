"""Erros de domínio do upload. As mensagens são fixas, em português, sem segredos nem caminhos."""

from __future__ import annotations


class UploadError(Exception):
    """Base. `status` é o código HTTP que a camada de rota usará; `file_index` aponta o arquivo."""

    status = 400
    code = "upload_error"

    def __init__(self, message: str, *, file_index: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.file_index = file_index


class TooLargeError(UploadError):
    status = 413
    code = "arquivo_grande_demais"


class UnsupportedTypeError(UploadError):
    status = 415
    code = "tipo_nao_suportado"


class InvalidImageError(UploadError):
    status = 422
    code = "imagem_invalida"


class TooManyFilesError(UploadError):
    status = 422
    code = "arquivos_demais"


class StorageUnavailableError(UploadError):
    status = 503
    code = "armazenamento_indisponivel"
