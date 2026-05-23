import qrcode
import binascii
from io import BytesIO

def char_code_at(s, i):
    """Mendapatkan nilai ASCII dari karakter pada posisi tertentu."""
    return ord(s[i])

def convert_crc16(qris_str):
    """Menghitung CRC16-CCITT (XModem) seperti dalam skrip PHP."""
    crc = 0xFFFF
    for char in qris_str:
        crc ^= char_code_at(qris_str, qris_str.index(char)) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF

    hex_crc = format(crc, '04X')
    return hex_crc.upper()

def generate_qris_dynamic(qris_static, nominal):
    """Mengubah QRIS statis menjadi dinamis dengan nominal tertentu."""
    qris_static = qris_static[:-4]  # Hapus CRC lama
    qris_dynamic = qris_static.replace("010211", "010212")  # Ubah tag transaksi

    split_qris = qris_dynamic.split("5802ID")  # Pisahkan bagian sebelum & sesudah tag negara
    nominal_tag = f"54{len(str(nominal)):02}{nominal}5802ID"  # Format nominal sesuai skrip PHP

    qris_final = split_qris[0] + nominal_tag + split_qris[1]  # Gabungkan ulang dengan nominal
    qris_final += convert_crc16(qris_final)  # Tambahkan CRC16

    return qris_final

def generate_qr_image(qris_string):
    """Membuat gambar QR Code dari string QRIS."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qris_string)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")

def generate_qr_with_amount(qris_base64, amount, unique_digits=None):
    """
    Generate QR code with dynamic amount and optional unique digits.

    Args:
        qris_base64: Base64 encoded QRIS string
        amount: Base payment amount
        unique_digits: Optional 3-digit unique number to add to amount

    Returns:
        tuple: (BytesIO object containing the QR image, actual_amount with unique digits)
        On error returns: (None, 0)
    """
    try:
        # Jika unique_digits diberikan, tambahkan ke amount
        actual_amount = amount
        if unique_digits is not None:
            # Ubah cara penambahan unique_digits: langsung tambahkan ke jumlah
            actual_amount = amount + unique_digits  # Langsung tambahkan sebagai integer

        qris_dynamic = generate_qris_dynamic(qris_base64, int(actual_amount))
        qr_image = generate_qr_image(qris_dynamic)

        # Convert to bytes
        img_byte_arr = BytesIO()
        qr_image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)

        return (img_byte_arr, actual_amount)
    except Exception as e:
        print(f"Error generating QR: {e}")
        # Return tuple dengan None untuk konsistensi
        return (None, 0)