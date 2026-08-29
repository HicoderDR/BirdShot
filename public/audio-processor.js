class BirdShotAudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(2048)
    this.index = 0
  }

  process(inputs) {
    const channel = inputs[0]?.[0]
    if (!channel) return true
    for (let index = 0; index < channel.length; index++) {
      this.buffer[this.index++] = channel[index]
      if (this.index === this.buffer.length) {
        this.port.postMessage(this.buffer)
        this.buffer = new Float32Array(2048)
        this.index = 0
      }
    }
    return true
  }
}

registerProcessor('birdshot-audio-processor', BirdShotAudioProcessor)
