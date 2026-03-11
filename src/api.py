from flask import Flask, jsonify
import sqlite3

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect('stamp_data.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/stamps', methods=['GET'])
def get_stamps():
    conn = get_db_connection()
    stamps = conn.execute('SELECT * FROM stamps').fetchall()
    conn.close()
    return jsonify([dict(ix) for ix in stamps]), 200

@app.route('/stamps/<int:stamp_id>', methods=['GET'])
def get_stamp(stamp_id):
    conn = get_db_connection()
    stamp = conn.execute('SELECT * FROM stamps WHERE id = ?', (stamp_id,)).fetchone()
    conn.close()
    if stamp is None:
        return jsonify({'error': 'Stamp not found'}), 404
    return jsonify(dict(stamp)), 200

if __name__ == '__main__':
    app.run(debug=True)