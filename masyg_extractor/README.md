# masyg-extractor-server
# masyg-extractor-server

"""

users (collection)
 └── {user_id} (document)
       ├── email: string                   // e.g., "jean@email.com"
       ├── hasUsedTrial: boolean           // e.g., false
       ├── isSubscribed: boolean           // e.g., false
       ├── password: string                // e.g., "pbkdf2:sha256:..."
       ├── username: string                // e.g., "Jean"
       ├── createdAt: Timestamp            // when the user was created
       └── updatedAt: Timestamp            // last update timestamp
       
       ├── groups (subcollection)
       │      └── {group_id} (document)
       │             ├── metadata: { ... }               // additional group-specific details
       │             └── files (subcollection)
       │                      └── {file_name} (document) → { ... }  // each file's parsed data
       │
       └── integrations (subcollection)
              ├── QuickBooks (collection)
              │      └── {group_id} (document)
              │             ├── {transaction_id} (document)
              │             │       ├── transactionType: string    // e.g., "Invoice"
              │             │       ├── docNumber: string          // e.g., "INV-12345"
              │             │       ├── customerId: string         // e.g., "12"
              │             │       ├── date: Timestamp            // e.g., Timestamp corresponding to 2025-02-14
              │             │       ├── amount: number             // e.g., 2415.0
              │             │       └── metadata: {                // additional integration-specific data
              │             │              ├── syncToken: string
              │             │              └── otherField: string
              │             └── {transaction_id} (document)
              │                     ├── transactionType: string    
              │                     ├── docNumber: string          
              │                     ├── customerId: string         
              │                     ├── date: Timestamp            
              │                     ├── amount: number             
              │                     └── metadata: { ... }
              │
              ├── Xero (collection)
              │      └── {group_id} (document)
              │             └── {transaction_id} (document)
              │                     ├── transactionType: string
              │                     ├── docNumber: string
              │                     ├── customerId: string
              │                     ├── date: Timestamp
              │                     ├── amount: number
              │                     └── metadata: { ... }
              │
              └── [Other integrations...]



creation of a  customer rules
here is the deal when to create a customer or not in QB: 
when the post resquest is sent 
a customer_name is sent int the request
you can assume a customer_name is required   in the request otherwise
stop the process and ask for the customer_name
but the customer_id might or might not be in the request

a) CUSTOMER ID IS IN THE REQUEST
if it in the request check  if it 
in firestore if it not save it there, then send the 
data to QB using that customer name and the customer id
if it failed send appropriate message to the user why it failed

b) CUSTOMER ID IS NOT IN THE REQUEST
if  the customer id is not in the request 
create a new customer in QB with that customer_name 
and save its customer_id return by quickbooks after 
creation its infos in firestore


creation of an  invoice rules
here is the deal when to create an invoice 
a) check if the invoice  exist in firestore by using the group_id/transaction_id 
also save the documber in QB related to as well
amd adjust acording existence that matching group_id/transaxtion_id
docnumber 
which is basically the group_id/filename assume the group_id{ filename will be in the request 
where the data where extracted from if it there 
tell the user that this invoice is already in QB

if it not create a new invoice and send it to qb


cleanedData  const input: ExtractedData = {
      group2: {
        metadata: {
          upload_time: "2024-10-01",
          file_count: 2,
          files: [
            { content: "pdf", filename: "file3.pdf" },
            { content: "pdf", filename: "file4.pdf" },
          ],
        },
        "file3.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor A",
          line_items: [
            { item_name: "A1", description: "Pipe 2 inch", quantity: 5, unit_price: "15.00" },
            { item_name: "A2", description: "Wrench", quantity: 2, unit_price: "25.50" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor B",
          line_items: [
            { item_name: "B1", description: "Hammer", quantity: 1, unit_price: "10.00" },
          ],
        },
      },
      group3: {
        metadata: {
          upload_time: "2024-10-01",
          file_count: 2,
          files: [
            { content: "pdf", filename: "file4.pdf" },
            { content: "pdf", filename: "file5.pdf" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor C",
          line_items: [
            { item_name: "C1", description: "Screwdriver Set", quantity: 3, unit_price: "8.99" },
          ],
        },
        "file5.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor D",
          line_items: [
            { item_name: "D1", description: "Pliers", quantity: 4, unit_price: "12.50" },
          ],
        },
      },
    };

    // Expected output: A flat object of uploaded files excluding metadata.
    const expectedOutput = {        
        "file3.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor A",
          line_items: [
            { item_name: "A1", description: "Pipe 2 inch", quantity: 5, unit_price: "15.00" },
            { item_name: "A2", description: "Wrench", quantity: 2, unit_price: "25.50" },
          ],
        },
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor B",
          line_items: [
            { item_name: "B1", description: "Hammer", quantity: 1, unit_price: "10.00" },
          ],
        },

     
        "file4.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor C",
          line_items: [
            { item_name: "C1", description: "Screwdriver Set", quantity: 3, unit_price: "8.99" },
          ],
        },
        "file5.pdf": {
          date: "2024-10-01",
          tax: "USD",
          vendor_name: "Vendor D",
          line_items: [
            { item_name: "D1", description: "Pliers", quantity: 4, unit_price: "12.50" },
          ],
        },
      },
    

