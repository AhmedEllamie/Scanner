from __future__ import annotations

import cv2
import numpy as np

from .geometry import order_points


class ManualSelector:
    def __init__(self, window_name: str) -> None:
        self.window_name = window_name
        self.points: list[tuple[int, int]] = []
        self.enabled = False
        self._source_size: tuple[int, int] = (1, 1)  # width, height
        self._display_size: tuple[int, int] = (1, 1)  # width, height

    def set_viewport(
        self,
        *,
        source_width: int,
        source_height: int,
        display_width: int,
        display_height: int,
    ) -> None:
        self._source_size = (max(1, int(source_width)), max(1, int(source_height)))
        self._display_size = (max(1, int(display_width)), max(1, int(display_height)))

    def _display_to_source(self, x: int, y: int) -> tuple[int, int]:
        src_w, src_h = self._source_size
        disp_w, disp_h = self._display_size

        sx = src_w / float(disp_w)
        sy = src_h / float(disp_h)
        mx = int(round(x * sx))
        my = int(round(y * sy))
        mx = max(0, min(src_w - 1, mx))
        my = max(0, min(src_h - 1, my))
        return mx, my

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _userdata: object) -> None:
        if not self.enabled:
            return
        if event == cv2.EVENT_LBUTTONDOWN and len(self.points) < 4:
            self.points.append(self._display_to_source(x, y))

    def reset(self) -> None:
        self.points.clear()

    def get_quad(self) -> np.ndarray | None:
        if len(self.points) != 4:
            return None
        return order_points(np.array(self.points, dtype=np.float32))

    def draw(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        for idx, (x, y) in enumerate(self.points):
            cv2.circle(out, (x, y), 6, (0, 255, 255), -1)
            cv2.putText(
                out,
                str(idx + 1),
                (x + 8, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
        if len(self.points) > 1:
            for i in range(len(self.points) - 1):
                cv2.line(out, self.points[i], self.points[i + 1], (255, 180, 0), 2, cv2.LINE_AA)
        if len(self.points) == 4:
            cv2.line(out, self.points[-1], self.points[0], (255, 180, 0), 2, cv2.LINE_AA)
        return out

