class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(4096)
    this.offset = 0
  }

  process(inputs) {
    const input = inputs[0]?.[0]
    if (!input) return true

    let sourceOffset = 0
    while (sourceOffset < input.length) {
      const count = Math.min(input.length - sourceOffset, this.buffer.length - this.offset)
      this.buffer.set(input.subarray(sourceOffset, sourceOffset + count), this.offset)
      this.offset += count
      sourceOffset += count
      if (this.offset === this.buffer.length) {
        const pcm = new Int16Array(this.buffer.length)
        for (let index = 0; index < this.buffer.length; index += 1) {
          const sample = Math.max(-1, Math.min(1, this.buffer[index]))
          pcm[index] = sample < 0 ? sample * 32768 : sample * 32767
        }
        this.port.postMessage(pcm.buffer, [pcm.buffer])
        this.offset = 0
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
