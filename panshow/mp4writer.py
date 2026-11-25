"""Writes MP4 video files (H.264 picture + AAC sound) with Windows' own Media Foundation.

Windows has these encoders built in, so Panshow needs no extra video library (the common ones
contain the GPL-licensed x264 encoder). If the graphics chip has a video encoder that Windows
offers, it is used; otherwise Windows' software encoder.

Pictures come in as NV12 (the usual video format: full-size brightness plane, then a half-size
plane of interleaved colour pairs), sound as 16-bit stereo PCM. All Media Foundation calls run
in one helper thread, so encoding overlaps with drawing the next frame.
"""
import ctypes
import logging
import queue
import threading
import uuid
from ctypes import POINTER, byref, c_int64, c_long, c_uint32, c_uint64, c_void_p

HRESULT = c_long


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _g(s):
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


# IDs from the Windows headers (mfapi.h, mfreadwrite.h, mfidl.h)
MF_MT_MAJOR_TYPE = _g("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = _g("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_AVG_BITRATE = _g("20332624-fb0d-4d9e-bd0d-cbf6786c102e")
MF_MT_INTERLACE_MODE = _g("e2724bb8-e676-4806-b4b2-a8d6efb44ccd")
MF_MT_FRAME_SIZE = _g("1652c33d-d6b2-4012-b834-72030849a37d")
MF_MT_FRAME_RATE = _g("c459a2e8-3d2c-4e44-b132-fee5156c7bb0")
MF_MT_PIXEL_ASPECT_RATIO = _g("c6376a1e-8d0a-4027-be45-6d9a0ad39bb6")
MF_MT_DEFAULT_STRIDE = _g("644b4e48-1e02-4516-b0eb-c01ca9d49ac6")
MF_MT_MPEG2_PROFILE = _g("ad76a80b-2d5c-4e0b-b375-64e520137036")
MF_MT_YUV_MATRIX = _g("3e23d450-2c75-4d25-a00e-b91670d12327")
MF_MT_VIDEO_PRIMARIES = _g("dbfbe4d7-0740-4ee0-8192-850ab0e21935")
MF_MT_TRANSFER_FUNCTION = _g("5fb0fce9-be5c-4935-a811-ec838f8eed93")
MF_MT_VIDEO_NOMINAL_RANGE = _g("c21b8ee5-b956-4071-8daf-325edf5cab11")
MF_MT_ALL_SAMPLES_INDEPENDENT = _g("c9173739-5e56-461c-b713-46fb995cb95f")
MF_MT_AUDIO_NUM_CHANNELS = _g("37e48bf5-645e-4c5b-89de-ada9e29b696a")
MF_MT_AUDIO_SAMPLES_PER_SECOND = _g("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
MF_MT_AUDIO_BITS_PER_SAMPLE = _g("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = _g("1aab75c8-cfef-451c-ab95-ac034b8e1731")
MF_MT_AUDIO_BLOCK_ALIGNMENT = _g("322de230-9eeb-43bd-ab7a-ff412251541d")
# encoder settings (codecapi.h)
CODECAPI_AVEncCommonRateControlMode = _g("1c0608e9-370c-4710-8a58-cb6181c42423")
CODECAPI_AVEncCommonQuality = _g("fcbf57a3-7ea5-4b0c-9644-69b40c39c391")
CODECAPI_AVEncCommonMeanBitRate = _g("f7222374-2144-4815-b550-a37f8e12ee52")
CODECAPI_AVEncCommonMaxBitRate = _g("9651eae4-39b9-4ebf-85ef-d7f444ec7465")
CODECAPI_AVEncCommonBufferSize = _g("0db96574-b6a4-4c8b-8106-3773de0310cd")
CODECAPI_AVEncMPVGOPSize = _g("95f31b26-95a4-41aa-9303-246a7fc6eef1")
RC_CBR, RC_PEAK_VBR, RC_VBR, RC_QUALITY = 0, 1, 2, 3
MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS = _g("a634a91c-822b-41b9-a494-4de4643612b0")
MF_TRANSCODE_CONTAINERTYPE = _g("150ff23f-4abc-478b-ac4f-e1916fba1cca")
MFTranscodeContainerType_MPEG4 = _g("dc6cd05d-b9d0-40ef-bd35-fa622c1ab28a")
MFMediaType_Video = _g("73646976-0000-0010-8000-00aa00389b71")
MFMediaType_Audio = _g("73647561-0000-0010-8000-00aa00389b71")
MFVideoFormat_H264 = _g("34363248-0000-0010-8000-00aa00389b71")
MFVideoFormat_NV12 = _g("3231564e-0000-0010-8000-00aa00389b71")
MFAudioFormat_AAC = _g("00001610-0000-0010-8000-00aa00389b71")
MFAudioFormat_PCM = _g("00000001-0000-0010-8000-00aa00389b71")

MF_VERSION = 0x00020070
INTERLACE_PROGRESSIVE = 2
H264_PROFILE_HIGH = 100
MATRIX_BT709, PRIMARIES_BT709, TRANSFER_709, RANGE_16_235 = 1, 2, 5, 2

# places of the functions in the interfaces' function tables
_RELEASE = 2
_SET_UINT32, _SET_UINT64, _SET_GUID = 21, 22, 24
_ADD_STREAM, _SET_INPUT_TYPE, _BEGIN_WRITING, _WRITE_SAMPLE, _FINALIZE = 3, 4, 5, 6, 11
_SET_SAMPLE_TIME, _SET_SAMPLE_DURATION, _ADD_BUFFER = 36, 38, 42
_LOCK, _UNLOCK, _SET_CURRENT_LENGTH = 3, 4, 6


class EncoderError(RuntimeError):
    pass


_protos = {}


def _call(obj, index, argtypes, *args, what="call"):
    key = (index, tuple(argtypes))
    proto = _protos.get(key)
    if proto is None:
        proto = _protos[key] = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    hr = proto(vtbl[index])(obj, *args)
    if hr < 0:
        raise EncoderError("%s failed (0x%08X)" % (what, hr & 0xFFFFFFFF))
    return hr


def _release(obj):
    if obj:
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents[_RELEASE])(obj)


def _u32(obj, key, v):
    _call(obj, _SET_UINT32, [POINTER(GUID), c_uint32], byref(key), c_uint32(int(v)), what="SetUINT32")


def _u64(obj, key, hi, lo):
    _call(obj, _SET_UINT64, [POINTER(GUID), c_uint64], byref(key), c_uint64((int(hi) << 32) | int(lo)),
          what="SetUINT64")


def _guid(obj, key, v):
    _call(obj, _SET_GUID, [POINTER(GUID), POINTER(GUID)], byref(key), byref(v), what="SetGUID")


class Mp4Writer:
    """write_video(nv12_bytes) once per frame; write_audio(int16 stereo bytes) as the sound
    goes along; close() finishes the file. Errors from the helper thread come back on the
    next call."""

    def __init__(self, path, width, height, fps=60, bitrate=None, audio_rate=None, hardware=True,
                 rate_control="peak"):
        """rate_control: "peak" = average `bitrate`, at most 1.5x in hard moments;
        "default" = whatever the encoder does by itself; ("quality", 0..100) = constant quality."""
        self.path, self.w, self.h, self.fps = path, int(width), int(height), int(fps)
        self.bitrate = int(bitrate or (40_000_000 if width * height > 2_500_000 else 14_000_000))
        self.audio_rate = int(audio_rate) if audio_rate else None
        self.hardware = hardware
        self.rate_control = rate_control
        self.used_settings = None
        self.frames = 0
        self.audio_samples = 0
        self.error = None
        self.used_hardware = None
        self.q = queue.Queue(maxsize=3)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait()
        self._check()

    # ---------------------------------------------------------------- public
    def write_video(self, data):
        self._check()
        self._put(("v", data))

    def write_audio(self, data):
        if self.audio_rate:
            self._check()
            self._put(("a", data))

    def close(self):
        """Finish the file (returns True if it was written without problems)."""
        if self.thread.is_alive():
            self._put(("end", None))
            self.thread.join()
        self._check()
        return True

    def abort(self):
        if self.thread.is_alive():
            self._put(("abort", None))
            self.thread.join(30)

    def _put(self, item):
        while True:
            try:
                self.q.put(item, timeout=0.5)
                return
            except queue.Full:
                if not self.thread.is_alive():
                    self._check()
                    raise EncoderError("the video encoder stopped")

    def _check(self):
        if self.error is not None:
            raise EncoderError(self.error)

    # ---------------------------------------------------------------- helper thread
    def _run(self):
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0)                 # multithreaded
        mfplat = ctypes.windll.mfplat
        started = False
        writer = None
        try:
            if mfplat.MFStartup(MF_VERSION, 0) < 0:
                raise EncoderError("Windows' video system (Media Foundation) is not available")
            started = True
            writer = None
            # graphics chip first; with our rate settings first (an encoder may refuse them)
            tries = [(hw, rc) for hw in ([True, False] if self.hardware else [False])
                     for rc in ([self.rate_control, "default"] if self.rate_control != "default" else ["default"])]
            last = None
            for hw, rc in tries:
                try:
                    writer = self._open(hw, rc)
                    self.used_hardware, self.used_settings = hw, rc
                    break
                except EncoderError as e:
                    last = e
                    logging.info("video encoder (%s, %s) could not start: %s",
                                 "graphics chip" if hw else "software", rc, e)
            if writer is None:
                raise EncoderError("no H.264 video encoder could be started (%s)" % last)
            logging.info("video: %dx%d %d fps, %.0f Mbit/s, %s encoder, rate control %s, sound %s", self.w, self.h,
                         self.fps, self.bitrate / 1e6, "graphics chip" if self.used_hardware else "software",
                         self.used_settings, "%d Hz" % self.audio_rate if self.audio_rate else "none")
            self.ready.set()
            frame_bytes = self.w * self.h * 3 // 2
            while True:
                kind, data = self.q.get()
                if kind == "v":
                    if len(data) != frame_bytes:
                        raise EncoderError("wrong frame size")
                    t0 = self.frames * 10_000_000 // self.fps
                    t1 = (self.frames + 1) * 10_000_000 // self.fps
                    self._write(writer, self.vidx, data, t0, t1 - t0)
                    self.frames += 1
                elif kind == "a":
                    n = len(data) // 4
                    if n:
                        t0 = self.audio_samples * 10_000_000 // self.audio_rate
                        t1 = (self.audio_samples + n) * 10_000_000 // self.audio_rate
                        self._write(writer, self.aidx, data, t0, t1 - t0)
                        self.audio_samples += n
                elif kind == "end":
                    _call(writer, _FINALIZE, [], what="Finalize")
                    break
                else:                                  # abort: leave the file unfinished
                    break
        except Exception as e:
            logging.exception("video encoder failed")
            self.error = str(e) or e.__class__.__name__
        finally:
            self.ready.set()
            _release(writer)
            if started:
                mfplat.MFShutdown()
            ole32.CoUninitialize()
            # let a waiting producer notice
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass

    def _media_type(self):
        mt = c_void_p()
        if ctypes.windll.mfplat.MFCreateMediaType(byref(mt)) < 0:
            raise EncoderError("MFCreateMediaType failed")
        return mt

    def _video_type(self, subtype):
        mt = self._media_type()
        _guid(mt, MF_MT_MAJOR_TYPE, MFMediaType_Video)
        _guid(mt, MF_MT_SUBTYPE, subtype)
        _u32(mt, MF_MT_INTERLACE_MODE, INTERLACE_PROGRESSIVE)
        _u64(mt, MF_MT_FRAME_SIZE, self.w, self.h)
        _u64(mt, MF_MT_FRAME_RATE, self.fps, 1)
        _u64(mt, MF_MT_PIXEL_ASPECT_RATIO, 1, 1)
        # standard HD colours (BT.709, TV levels 16-235), so TVs show the colours as intended
        _u32(mt, MF_MT_YUV_MATRIX, MATRIX_BT709)
        _u32(mt, MF_MT_VIDEO_PRIMARIES, PRIMARIES_BT709)
        _u32(mt, MF_MT_TRANSFER_FUNCTION, TRANSFER_709)
        _u32(mt, MF_MT_VIDEO_NOMINAL_RANGE, RANGE_16_235)
        return mt

    def _encoder_settings(self, rc):
        """Settings handed to the encoder (None = its own defaults)."""
        if rc == "default":
            return None
        enc = c_void_p()
        if ctypes.windll.mfplat.MFCreateAttributes(byref(enc), 6) < 0:
            raise EncoderError("MFCreateAttributes failed")
        if isinstance(rc, tuple) and rc[0] == "quality":
            _u32(enc, CODECAPI_AVEncCommonRateControlMode, RC_QUALITY)
            _u32(enc, CODECAPI_AVEncCommonQuality, rc[1])
        else:
            _u32(enc, CODECAPI_AVEncCommonRateControlMode, RC_PEAK_VBR)
            _u32(enc, CODECAPI_AVEncCommonMeanBitRate, self.bitrate)
            _u32(enc, CODECAPI_AVEncCommonMaxBitRate, int(self.bitrate * 1.5))
        _u32(enc, CODECAPI_AVEncMPVGOPSize, self.fps)              # a key frame every second
        return enc

    def _open(self, hardware, rc="default"):
        mfplat, mfrw = ctypes.windll.mfplat, ctypes.windll.mfreadwrite
        attrs, writer = c_void_p(), c_void_p()
        types = []
        try:
            if mfplat.MFCreateAttributes(byref(attrs), 2) < 0:
                raise EncoderError("MFCreateAttributes failed")
            _u32(attrs, MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, 1 if hardware else 0)
            _guid(attrs, MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4)
            hr = mfrw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(self.path), None, attrs, byref(writer))
            if hr < 0:
                raise EncoderError("could not create the file (0x%08X)" % (hr & 0xFFFFFFFF))
            out = self._video_type(MFVideoFormat_H264)
            types.append(out)
            _u32(out, MF_MT_AVG_BITRATE, self.bitrate)
            _u32(out, MF_MT_MPEG2_PROFILE, H264_PROFILE_HIGH)
            idx = ctypes.c_ulong()
            _call(writer, _ADD_STREAM, [c_void_p, POINTER(ctypes.c_ulong)], out, byref(idx), what="AddStream(video)")
            self.vidx = idx.value
            inp = self._video_type(MFVideoFormat_NV12)
            types.append(inp)
            _u32(inp, MF_MT_DEFAULT_STRIDE, self.w)
            _u32(inp, MF_MT_ALL_SAMPLES_INDEPENDENT, 1)
            enc = self._encoder_settings(rc)
            if enc is not None:
                types.append(enc)
            _call(writer, _SET_INPUT_TYPE, [ctypes.c_ulong, c_void_p, c_void_p], self.vidx, inp, enc,
                  what="SetInputMediaType(video)")
            if self.audio_rate:
                a_out = self._media_type()
                types.append(a_out)
                _guid(a_out, MF_MT_MAJOR_TYPE, MFMediaType_Audio)
                _guid(a_out, MF_MT_SUBTYPE, MFAudioFormat_AAC)
                _u32(a_out, MF_MT_AUDIO_BITS_PER_SAMPLE, 16)
                _u32(a_out, MF_MT_AUDIO_SAMPLES_PER_SECOND, self.audio_rate)
                _u32(a_out, MF_MT_AUDIO_NUM_CHANNELS, 2)
                _u32(a_out, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 24000)      # 192 kbit/s
                _call(writer, _ADD_STREAM, [c_void_p, POINTER(ctypes.c_ulong)], a_out, byref(idx),
                      what="AddStream(sound)")
                self.aidx = idx.value
                a_in = self._media_type()
                types.append(a_in)
                _guid(a_in, MF_MT_MAJOR_TYPE, MFMediaType_Audio)
                _guid(a_in, MF_MT_SUBTYPE, MFAudioFormat_PCM)
                _u32(a_in, MF_MT_AUDIO_BITS_PER_SAMPLE, 16)
                _u32(a_in, MF_MT_AUDIO_SAMPLES_PER_SECOND, self.audio_rate)
                _u32(a_in, MF_MT_AUDIO_NUM_CHANNELS, 2)
                _u32(a_in, MF_MT_AUDIO_BLOCK_ALIGNMENT, 4)
                _u32(a_in, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, self.audio_rate * 4)
                _call(writer, _SET_INPUT_TYPE, [ctypes.c_ulong, c_void_p, c_void_p], self.aidx, a_in, None,
                      what="SetInputMediaType(sound)")
            _call(writer, _BEGIN_WRITING, [], what="BeginWriting")
            # Make sure the encoder really accepts frames of this size (some refuse only now).
            # This black frame is the video's first frame (frame 0); callers continue from
            # self.frames.
            black = bytes([16]) * (self.w * self.h) + bytes([128]) * (self.w * self.h // 2)
            self._write(writer, self.vidx, black, 0, 10_000_000 // self.fps)
            self.frames = 1
            w, writer = writer, None
            return w
        finally:
            for t in types:
                _release(t)
            _release(attrs)
            _release(writer)

    @staticmethod
    def _write(writer, stream, data, t, dur):
        mfplat = ctypes.windll.mfplat
        n = len(data)
        buf, sample = c_void_p(), c_void_p()
        try:
            if mfplat.MFCreateMemoryBuffer(ctypes.c_ulong(n), byref(buf)) < 0:
                raise EncoderError("out of memory for a video frame")
            ptr = ctypes.POINTER(ctypes.c_ubyte)()
            _call(buf, _LOCK, [ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), c_void_p, c_void_p],
                  byref(ptr), None, None, what="Lock")
            try:
                src = (ctypes.c_char * n).from_buffer(data) if isinstance(data, bytearray) else data
                ctypes.memmove(ptr, src, n)
            finally:
                _call(buf, _UNLOCK, [], what="Unlock")
            _call(buf, _SET_CURRENT_LENGTH, [ctypes.c_ulong], n, what="SetCurrentLength")
            if mfplat.MFCreateSample(byref(sample)) < 0:
                raise EncoderError("MFCreateSample failed")
            _call(sample, _ADD_BUFFER, [c_void_p], buf, what="AddBuffer")
            _call(sample, _SET_SAMPLE_TIME, [c_int64], t, what="SetSampleTime")
            _call(sample, _SET_SAMPLE_DURATION, [c_int64], dur, what="SetSampleDuration")
            _call(writer, _WRITE_SAMPLE, [ctypes.c_ulong, c_void_p], stream, sample, what="WriteSample")
        finally:
            _release(sample)
            _release(buf)
