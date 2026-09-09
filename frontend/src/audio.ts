import type { SessionSnapshot } from './types'

export interface AudioCapture {
  stop: () => Promise<void>
  sampleRate: number
}

export async function listAudioInputs(): Promise<MediaDeviceInfo[]> {
  const devices = await navigator.mediaDevices.enumerateDevices()
  return devices.filter((device) => device.kind === 'audioinput')
}

export async function startAudioCapture(
  sessionId: string,
  deviceId: string,
  onSnapshot: (snapshot: SessionSnapshot) => void,
  onError: (message: string) => void,
): Promise<AudioCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      deviceId: deviceId ? { exact: deviceId } : undefined,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
    video: false,
  })
  const context = new AudioContext({ sampleRate: 16_000 })
  await context.audioWorklet.addModule('/pcm-worklet.js')
  const source = context.createMediaStreamSource(stream)
  const worklet = new AudioWorkletNode(context, 'pcm-capture')
  const silentGain = context.createGain()
  silentGain.gain.value = 0
  source.connect(worklet)
  worklet.connect(silentGain)
  silentGain.connect(context.destination)

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/sessions/${sessionId}`)
  socket.binaryType = 'arraybuffer'
  await new Promise<void>((resolve, reject) => {
    socket.addEventListener('open', () => resolve(), { once: true })
    socket.addEventListener('error', () => reject(new Error('无法连接录音后端')), { once: true })
  })

  socket.addEventListener('message', (event) => {
    try {
      onSnapshot(JSON.parse(String(event.data)) as SessionSnapshot)
    } catch {
      onError('收到无法解析的会话状态')
    }
  })
  socket.addEventListener('close', (event) => {
    if (event.code !== 1000) onError('录音连接已断开，已录音频仍保存在后端')
  })

  worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(event.data)
  }
  await context.resume()

  return {
    sampleRate: context.sampleRate,
    stop: async () => {
      worklet.port.onmessage = null
      source.disconnect()
      worklet.disconnect()
      silentGain.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      await context.close()
      if (socket.readyState === WebSocket.OPEN) socket.close(1000, 'recording stopped')
    },
  }
}
