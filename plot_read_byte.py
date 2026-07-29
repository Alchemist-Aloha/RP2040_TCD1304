import matplotlib.pyplot as plt
import serial
import numpy as np
import struct

PORT = 'COM14'  # Replace with your COM port
START_MARKER = 0xAA55

def read_binary_capture(ser: serial.Serial) -> np.ndarray:
    # Flush any pending data
    ser.reset_input_buffer()
    
    # Look for start marker
    while True:
        # Read one byte at a time to avoid misalignment
        b1 = ser.read(1)
        if not b1:
            continue
            
        if b1[0] == 0x55:  # First byte of marker
            b2 = ser.read(1)
            if not b2:
                continue
                
            if b2[0] == 0xAA:  # Second byte of marker
                break
    
    # Read header
    header_data = ser.read(6)  # 3 more uint16 values (6 bytes)
    if len(header_data) < 6:
        return None
    
    protocol_version, data_length, reserved = struct.unpack('<HHH', header_data)
    
    # Read the actual data
    data_bytes = ser.read(data_length * 2)  # 2 bytes per uint16
    if len(data_bytes) < data_length * 2:
        return None
    
    # Read checksum
    checksum_bytes = ser.read(2)
    if len(checksum_bytes) < 2:
        return None
    
    # Parse data
    data = np.array(struct.unpack(f'<{data_length}H', data_bytes))
    received_checksum = struct.unpack('<H', checksum_bytes)[0]
    
    # # Verify checksum
    # calculated_checksum = np.sum(data) & 0xFFFF
    # if calculated_checksum != received_checksum:
    #     print(f"Checksum error: expected {received_checksum}, got {calculated_checksum}")
    #     return None
    
    return data

# Main code to read and plot the spectrum
try:
    with serial.Serial(PORT, 115200, timeout=1) as ser:
        plt.ion()  # Turn on interactive mode
        fig, ax = plt.subplots(figsize=(10, 6))
        graph, = plt.plot([], [], label='ADC Values')
        plt.xlabel('Sample Number')
        plt.ylabel('ADC Value')
        plt.title('Captured Spectrum')
        plt.grid(True)

        while True:
            print("Waiting for data...")
            capture_buf = read_binary_capture(ser)
            
            if capture_buf is not None:
                print(f"Captured {len(capture_buf)} samples.")
                graph.set_data(np.arange(len(capture_buf)), capture_buf)
                ax.relim()
                ax.autoscale_view()
                plt.draw()
                plt.pause(0.02)
            else:
                print("Failed to read valid data")
                plt.pause(0.5)
        
except serial.SerialException as e:
    print(f"Serial port error: {e}")
except KeyboardInterrupt:
    print("Exiting program...")
