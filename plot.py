import matplotlib.pyplot as plt
import serial  # To read data from serial port
import numpy as np

PORT = 'COM14'  # Replace with your COM port

# Function to read capture_buf data from the serial port
def read_capture_buf(ser):
    buf = []
    while True:
        try:
            line = ser.readline().decode('utf-8').strip()
        except Exception as e:
            print(f"Error reading from serial port: {e}")
            continue
        if line == "Capture finished":
            print("start record")
            break
    while True:
        try:
            line = ser.readline().decode('utf-8').strip()
        except Exception as e:
            print(f"Error reading from serial port: {e}")
            continue
        if line == "Capture finished":
            print("end record")
            break
        try:
            values = line.split(",")  # Splitting values by commas
            buf += [int(val) for val in values if val.strip()]
        except ValueError:
            pass
    return buf


# Main code to read and plot the spectrum
try:
    with serial.Serial(PORT, 115200, timeout=1) as ser:

        capture_buf = read_capture_buf(ser)
        plt.ion()  # Turn on interactive mode
        # fig, ax = plt.subplots(figsize=(10, 6))
        # Initial plot setup
        graph = plt.plot(np.arange(len(capture_buf)),capture_buf, label='ADC Values')[0]  # Initialize with zeros
        plt.xlabel('Sample Number')
        plt.ylabel('ADC Value')
        # plt.ylim(0, 255)
        plt.title('Captured Spectrum')
        # ax.grid(True)
        # ax.legend()

        while True:
            # Reading capture buffer from serial
            print("Reading capture buffer data...")
            capture_buf = read_capture_buf(ser)

            # Plotting the captured buffer
            if capture_buf:
                print(f"Captured {len(capture_buf)} samples.")
                # removing the older graph
                graph.remove()
                graph = plt.plot(np.arange(len(capture_buf)),capture_buf, label='ADC Values')[0]
                plt.xlabel('Sample Number')
                plt.ylabel('ADC Value')
                # plt.ylim(0, 255)
                plt.title('Captured Spectrum')
                plt.pause(0.02)  # Pause briefly to allow the plot to update
                y_axis = []

            else:
                graph.remove()
                print("No data to plot.")
                continue
        
except serial.SerialException as e:
    print(f"Serial port error: {e}")

except KeyboardInterrupt:
    print("Exiting program...")
