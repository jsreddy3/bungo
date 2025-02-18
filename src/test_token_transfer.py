from web3 import Web3
from eth_account import Account
import json

USE_MAINNET = False # CAREFUL: This will send real money

# ABI for ERC20 token (minimal interface for transfer)
ERC20_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    }
]

def test_token_transfer():
    # Connect to World Chain Sepolia
    rpc_url = 'https://worldchain-sepolia.g.alchemy.com/v2/jga2_08NETcJgODTKZanD1bHh1q0arAz'
    if USE_MAINNET:
        rpc_url = 'https://worldchain-mainnet.g.alchemy.com/v2/jga2_08NETcJgODTKZanD1bHh1q0arAz'
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    # Check if we're connected to the network
    if not w3.is_connected():
        print(f"Failed to connect to the network at {rpc_url}")
        return

    print(f"Successfully connected to World Chain {'Mainnet' if USE_MAINNET else 'Sepolia'}")
    print(f"Current block number: {w3.eth.block_number}")
    
    # Contract and account details
    contract_address = '0x261EEE06b473F59E053D337c990A893cC34b3856'  # The original contract where tokens were minted
    private_key = '0x61037aa2f9afad005d602f743b08a255fe829762a93576f7f41bc8d72e7391e3'
    
    # Check if there's code at the contract address
    contract_code = w3.eth.get_code(Web3.to_checksum_address(contract_address))
    if contract_code == b'':
        print(f"No contract code found at address {contract_address}")
        print("This address might be a regular wallet address or the contract might not be deployed on this network")
        return
    
    print(f"Contract code found at {contract_address}")
    
    # Create account object from private key
    account = Account.from_key(private_key)
    print(f"\nSender address: {account.address}")
    
    # Get the chain ID
    chain_id = w3.eth.chain_id
    print(f"Connected to chain ID: {chain_id}")
    
    # Get ETH balance of the account
    eth_balance = w3.eth.get_balance(account.address)
    print(f"Account ETH balance: {w3.from_wei(eth_balance, 'ether')} ETH")

    # Create contract instance
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=ERC20_ABI)
    
    # Recipient address
    recipient = "0x47b60086844B213E4671b5C64db849A3b29c5fCC"
    
    try:
        # First, let's check the token's decimals
        try:
            token_decimals = contract.functions.decimals().call()
            print(f"\nToken decimals: {token_decimals}")
        except Exception as e:
            print(f"Could not get token decimals, using 18 as per minting code: {str(e)}")
            token_decimals = 18

        # Get current balance
        balance = contract.functions.balanceOf(account.address).call()
        print(f"Current balance: {balance / 10**token_decimals} tokens")

        # Amount to transfer (10 tokens)
        raw_amount = 10  # The human-readable amount
        amount = raw_amount * 10**token_decimals
        print(f"\nTransferring {raw_amount} tokens")
        print(f"Raw amount with {token_decimals} decimals: {amount}")

        # Build transfer transaction
        nonce = w3.eth.get_transaction_count(account.address)
        
        # Get current gas price and use the minimum acceptable (base fee)
        base_fee = w3.eth.get_block('latest')['baseFeePerGas']
        priority_fee = 1_000_000_000  # 1 Gwei priority fee
        max_fee = 2 * base_fee + priority_fee  # Max fee formula
        
        print(f"Base fee: {base_fee / 10**9} Gwei")
        print(f"Priority fee: {priority_fee / 10**9} Gwei")
        print(f"Max fee: {max_fee / 10**9} Gwei")

        # Build the transaction
        transfer_txn = contract.functions.transfer(
            Web3.to_checksum_address(recipient),
            amount
        ).build_transaction({
            'chainId': 4801 if not USE_MAINNET else 480,  # 4801 is Sepolia, 480 is Mainnet
            'nonce': nonce,
            'gasPrice': w3.eth.gas_price,  # Use legacy gas price instead of EIP-1559
            'gas': 100000,  # Initial gas estimate
            'from': account.address
        })

        print("\nTransaction details:")
        for key, value in transfer_txn.items():
            print(f"{key}: {value}")

        # Estimate gas for this specific transaction
        try:
            estimated_gas = w3.eth.estimate_gas(transfer_txn)
            print(f"\nEstimated gas required: {estimated_gas}")
            transfer_txn['gas'] = estimated_gas
        except Exception as e:
            print(f"Error estimating gas: {str(e)}")
            print("Using default gas limit of 100000")

        # Calculate and display total gas cost (worst case)
        total_gas_cost_wei = estimated_gas * w3.eth.gas_price
        total_gas_cost_eth = w3.from_wei(total_gas_cost_wei, 'ether')
        print(f"Maximum gas cost: {total_gas_cost_eth} ETH")

        print("\nSigning transaction...")
        # Sign transaction
        signed_txn = w3.eth.account.sign_transaction(
            transfer_txn,
            private_key=private_key
        )
        
        print("\nSigned transaction details:")
        print(f"r: {getattr(signed_txn, 'r', 'Not found')}")
        print(f"s: {getattr(signed_txn, 's', 'Not found')}")
        print(f"v: {getattr(signed_txn, 'v', 'Not found')}")
        print(f"rawTransaction: {getattr(signed_txn, 'rawTransaction', 'Not found')}")
        print(f"hash: {getattr(signed_txn, 'hash', 'Not found')}")

        # Send transaction
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)  # Try raw_transaction instead of rawTransaction
        print(f"Transaction sent! Hash: {tx_hash.hex()}")
        
        # Wait for transaction receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Transaction status: {'Success' if receipt['status'] == 1 else 'Failed'}")
        
        # Check new balance
        new_balance = contract.functions.balanceOf(account.address).call()
        print(f"New balance: {new_balance / 10**token_decimals} tokens")

    except Exception as e:
        print(f"Error occurred: {str(e)}")

if __name__ == "__main__":
    test_token_transfer() 